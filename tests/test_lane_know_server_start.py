"""Lane KNOW: the server loads the public knowledge index at start, or says why not.

Human decision 2026-10-24 (decision 4): with ``VOINEY_LAB_PUBLIC_KNOWLEDGE_ENABLED``
the server loads the Moss index once in ``lifespan``; without the setting
nothing is built; with it but without ``MOSS_PROJECT_ID``/``MOSS_PROJECT_KEY``,
or when the load fails, only this tier is off, the reason is the setting's
name, and the rest of the server runs. ``/readyz`` and the ``ready`` event
report that state, content-free. Moss itself is never reached: the loader
is replaced by fakes.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from unittest.mock import patch

import httpx

from voiney_lab import server as server_module
from voiney_lab.public_knowledge import (
    ENABLED_SETTING,
    INDEX_SETTING,
    MOSS_PROJECT_ID_SETTING,
    KnowledgeUnavailable,
    LoadReport,
)

QUIET = {"log_protocol_catalog_runtime_configuration": lambda: None}
OFF = {ENABLED_SETTING: "false", MOSS_PROJECT_ID_SETTING: "", "MOSS_PROJECT_KEY": ""}
#: What /readyz needs to answer 200 (as tests/test_server_helpers.py sets it).
READY_ENV = {
    "VOINEY_LAB_USAGE_SCOPE": "source_protocol_only",
    "VOINEY_LAB_WORKSPACE_ENABLED": "true",
    "VOINEY_LAB_WORKSPACE_DATA_DIR": tempfile.mkdtemp(),
    "VOINEY_LAB_PROTOCOL_ENABLED": "false",
    "VOINEY_LAB_EXPERIMENT_REPORTS_ENABLED": "false",
}


class _FakeSource:
    tier = "public"

    def __init__(self) -> None:
        self.closed = False
        self.load_report = LoadReport(index_name="idx", elapsed_ms=12.5, glossary_terms=321)

    async def close(self) -> None:
        self.closed = True


def _run_lifespan(env: dict[str, str], *, during=None, **patches):
    async def go():
        async with server_module.lifespan(server_module.app):
            if during is not None:
                return await during()
        return None

    with patch.dict("os.environ", env), patch.multiple(server_module, **QUIET, **patches):
        return asyncio.run(go())


async def _readyz():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server_module.app),
                                 base_url="http://test") as client:
        return await client.get("/readyz")


class StartTests(unittest.TestCase):
    def tearDown(self) -> None:
        server_module.app.state.public_knowledge = None
        server_module.app.state.public_knowledge_status = None

    def test_off_builds_nothing_and_reports_disabled(self):
        calls: list[object] = []

        async def never(settings):
            calls.append(settings)

        _run_lifespan(OFF, start_public_knowledge=never)
        self.assertEqual(calls, [])
        self.assertIsNone(server_module.app.state.public_knowledge)
        self.assertEqual(server_module._public_knowledge_capability(),
                         {"status": "disabled", "authority": "public_reference", "index": None, "reason": None})

    def test_on_without_the_project_id_is_unavailable_by_that_name_and_the_server_still_starts(self):
        env = {ENABLED_SETTING: "true", MOSS_PROJECT_ID_SETTING: "", "MOSS_PROJECT_KEY": "k",
               "VOINEY_LAB_OCR_PROVIDERS": ""}
        _run_lifespan(env)
        status = server_module.app.state.public_knowledge_status
        self.assertEqual((status["enabled"], status["loaded"], status["reason"]), (True, False, MOSS_PROJECT_ID_SETTING))
        self.assertIsNone(server_module.app.state.public_knowledge)
        capability = server_module._public_knowledge_capability()
        self.assertEqual(capability["status"], "unavailable")
        self.assertEqual(capability["reason"], MOSS_PROJECT_ID_SETTING)
        self.assertIsNone(capability["index"])

    def test_a_failed_load_names_the_index_setting_and_nothing_else_breaks(self):
        async def failing(settings):
            raise KnowledgeUnavailable(INDEX_SETTING, "moss load failed: NETWORK")

        env = {ENABLED_SETTING: "true", MOSS_PROJECT_ID_SETTING: "p", "MOSS_PROJECT_KEY": "k"}
        _run_lifespan(env, start_public_knowledge=failing)
        self.assertIsNone(server_module.app.state.public_knowledge)
        self.assertEqual(server_module._public_knowledge_capability()["reason"], INDEX_SETTING)
        self.assertEqual(server_module._public_knowledge_capability()["status"], "unavailable")

    def test_a_malformed_switch_is_reported_by_name_not_read_as_off(self):
        _run_lifespan({ENABLED_SETTING: "maybe"})
        capability = server_module._public_knowledge_capability()
        self.assertEqual(capability["status"], "unavailable")
        self.assertIn(ENABLED_SETTING, capability["reason"])

    def test_a_loaded_index_is_kept_reported_without_credentials_and_closed_at_shutdown(self):
        source = _FakeSource()
        seen: list = []

        async def loader(settings):
            seen.append(settings)
            return source

        async def during():
            self.assertIs(server_module.app.state.public_knowledge, source)
            status = server_module.app.state.public_knowledge_status
            self.assertEqual((status["enabled"], status["loaded"], status["index"]), (True, True, "voiney-public-knowledge"))
            self.assertEqual(status["load_ms"], 12.5)
            self.assertEqual(status["glossary_terms"], 321)
            self.assertEqual(server_module._public_knowledge_capability(), {
                "status": "enabled", "authority": "public_reference", "index": "voiney-public-knowledge",
                "reason": None})
            self.assertNotIn("secret-key-value", json.dumps(status))
            ready = await _readyz()
            self.assertEqual(ready.status_code, 200)
            self.assertEqual(ready.json()["capabilities"]["public_knowledge"], "enabled")
            self.assertNotIn("secret-key-value", ready.text)

        env = {**READY_ENV, ENABLED_SETTING: "true", MOSS_PROJECT_ID_SETTING: "project-id-value",
               "MOSS_PROJECT_KEY": "secret-key-value"}
        _run_lifespan(env, during=during, start_public_knowledge=loader)
        self.assertEqual(seen[0].project_id, "project-id-value")
        self.assertIsNone(seen[0].cache_dir)  # no catalog folder configured: Moss's own default
        self.assertTrue(source.closed)
        self.assertIsNone(server_module.app.state.public_knowledge)

    def test_the_cache_sits_beside_the_protocol_catalog_when_there_is_one(self):
        seen: list = []

        async def loader(settings):
            seen.append(settings)
            return _FakeSource()

        env = {ENABLED_SETTING: "true", MOSS_PROJECT_ID_SETTING: "p", "MOSS_PROJECT_KEY": "k",
               "VOINEY_LAB_PROTOCOL_ENABLED": "true", "VOINEY_LAB_PROTOCOL_DATA_DIR": "/absolute/ignored/runtime/catalog"}
        _run_lifespan(env, start_public_knowledge=loader)
        self.assertEqual(seen[0].cache_dir, "/absolute/ignored/runtime/catalog/public-knowledge-cache")

    def test_readyz_says_disabled_when_the_tier_is_off(self):
        server_module.app.state.public_knowledge_status = {"enabled": False, "loaded": False, "index": None,
                                                           "reason": None}
        with patch.dict("os.environ", {**READY_ENV, **OFF}):
            ready = asyncio.run(_readyz())
        self.assertEqual(ready.status_code, 200)
        self.assertEqual(ready.json()["capabilities"]["public_knowledge"], "disabled")


if __name__ == "__main__":
    unittest.main()
