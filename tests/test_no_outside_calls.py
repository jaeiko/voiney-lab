"""Under pytest nothing reaches a provider, whatever keys exist (lane M1, decision 6).

Real keys for xAI, OpenAI, Anthropic, Gemini and the OCR services may sit in
the shell or in the repository .env. Under pytest:

* the server and the worker do not read the repository .env (only the
  old-setting-name check looks at it, in tests/conftest.py);
* tests/conftest.py takes the provider and OCR keys out of the environment
  before any test module is imported;
* a connection to anything but this machine fails at once -- even an SDK
  client given a key cannot send a request.
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.conftest import OutsideNetworkBlocked, PROVIDER_SECRET_NAMES

ROOT = Path(__file__).resolve().parents[1]
#: TEST-NET-3 (RFC 5737): never routed, and no DNS lookup is needed.
OUTSIDE = "203.0.113.10"


def _caused_by(error: BaseException, kind: type[BaseException]) -> bool:
    seen: set[int] = set()
    while error is not None and id(error) not in seen:
        if isinstance(error, kind):
            return True
        seen.add(id(error))
        error = error.__cause__ or error.__context__
    return False


class KeysAreNotInTheTestProcessTests(unittest.TestCase):
    def test_provider_and_ocr_keys_are_removed(self) -> None:
        for name in (
            "XAI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY",
            "VOINEY_LAB_CLOVA_OCR_SECRET", "VOINEY_LAB_GOOGLE_VISION_API_KEY",
        ):
            with self.subTest(name=name):
                self.assertIn(name, PROVIDER_SECRET_NAMES)
                self.assertNotIn(name, os.environ)

    def test_the_server_does_not_read_the_repository_dotenv(self) -> None:
        from voiney_lab import server

        self.assertTrue(os.environ.get("PYTEST_VERSION"))
        with patch.object(server, "load_dotenv") as load:
            self.assertFalse(server._load_project_environment())
            load.assert_not_called()
            server._load_project_environment(ROOT / "explicit.env")
            load.assert_called_once()

    def test_the_worker_does_not_read_the_repository_dotenv(self) -> None:
        probe = (
            "import dotenv; calls = []; "
            "dotenv.load_dotenv = lambda *a, **k: calls.append(a) or False; "
            "import voiney_lab.worker; print(len(calls))"
        )
        for pytest_version, expected in (("9", "0"), (None, "1")):
            environment = {k: v for k, v in os.environ.items() if k != "PYTEST_VERSION"}
            if pytest_version is not None:
                environment["PYTEST_VERSION"] = pytest_version
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            with self.subTest(pytest=pytest_version):
                result = subprocess.run(
                    [sys.executable, "-c", probe], cwd=ROOT, env=environment,
                    capture_output=True, text=True, timeout=120,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), expected)


class NoConnectionLeavesThisMachineTests(unittest.TestCase):
    def test_an_outside_address_is_refused_at_once(self) -> None:
        with self.assertRaises(OutsideNetworkBlocked):
            socket.create_connection((OUTSIDE, 443), timeout=5)

    def test_this_machine_is_still_reachable(self) -> None:
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        accepted = threading.Thread(target=lambda: server.accept()[0].close())
        accepted.start()
        try:
            socket.create_connection(server.getsockname(), timeout=5).close()
        finally:
            accepted.join(5)
            server.close()

    def test_sdk_clients_with_a_key_still_send_nothing(self) -> None:
        import anthropic
        import openai
        from google import genai
        from google.genai import types

        async def xai() -> None:
            client = openai.AsyncOpenAI(
                api_key="test-only-not-a-key", base_url=f"https://{OUTSIDE}/v1", max_retries=0,
            )
            await client.chat.completions.create(model="grok-4.6", messages=[{"role": "user", "content": "x"}])

        async def claude() -> None:
            client = anthropic.AsyncAnthropic(
                api_key="test-only-not-a-key", base_url=f"https://{OUTSIDE}", max_retries=0,
            )
            await client.messages.create(
                model="claude-haiku-4-5-20251001", max_tokens=8,
                messages=[{"role": "user", "content": "x"}],
            )

        async def gemini() -> None:
            client = genai.Client(
                api_key="test-only-not-a-key",
                http_options=types.HttpOptions(base_url=f"https://{OUTSIDE}/"),
            )
            await client.aio.models.generate_content(model="gemini-3.8-flash", contents="x")

        for name, call in (("xai/openai", xai), ("anthropic", claude), ("google", gemini)):
            with self.subTest(provider=name):
                with self.assertRaises(Exception) as caught:
                    asyncio.run(asyncio.wait_for(call(), timeout=30))
                self.assertTrue(
                    _caused_by(caught.exception, OutsideNetworkBlocked),
                    f"{name}: {type(caught.exception).__name__}",
                )


if __name__ == "__main__":
    unittest.main()
