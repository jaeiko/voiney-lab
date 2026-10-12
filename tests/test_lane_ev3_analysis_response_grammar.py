"""An experimental switch that sends Gemini the analysis schema as its response grammar (lane EV3, decision 2).

Human decision 2026-10-12. The analysis schema (196 properties) is sent to
Gemini as an instruction under JSON mode, not as a response grammar: lane P3
(2026-10-05) measured Vertex AI refusing it with 400 INVALID_ARGUMENT, and the
adapter has since sent any schema over GEMINI_MAX_SCHEMA_PROPERTIES that way.
Lane M3 then measured a fifth to a third of Gemini's analysis answers not
closing as JSON. To compare against decision 1 on a small live sample,
``VOINEY_LAB_ANALYSIS_RESPONSE_GRAMMAR`` (default false) makes the analysis
role's Gemini request carry its schema as ``response_json_schema`` whatever its
size. Off, every request is as before; other roles never change. The server
validates the reply exactly as before either way. Contract-tested against a
fake SDK client, not live-tested here.
"""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from tests.test_structured_output_providers import SMALL, SyncFake, call, response_format
from voiney_lab import model_providers as mp
from voiney_lab.experiment_protocol_analysis import ANALYSIS_PROVIDER_RESPONSE_SCHEMA
from voiney_lab.model_providers import RoleModel, chat_client
from voiney_lab.setting_names import BY_NAME

SETTING = "VOINEY_LAB_ANALYSIS_RESPONSE_GRAMMAR"


def switch(value: str | None):
    environment = {k: v for k, v in os.environ.items() if k != SETTING}
    if value is not None:
        environment[SETTING] = value
    return patch.dict(os.environ, environment, clear=True)


def gemini_config(role: str = "analysis", schema: dict = ANALYSIS_PROVIDER_RESPONSE_SCHEMA) -> dict:
    fake = SyncFake("google")
    client = chat_client(RoleModel(role, "google", "gemini-3.8-flash", "low"),
                         asynchronous=False, sdk_client=fake)
    client.chat.completions.create(
        model="gemini-3.8-flash",
        messages=[{"role": "system", "content": "RULES"}, {"role": "user", "content": "DOC"}],
        response_format=response_format(schema))
    return fake.requests[0]["config"]


class TheSwitchTests(unittest.TestCase):
    def test_off_by_default_the_analysis_schema_stays_an_instruction(self) -> None:
        for value in (None, "", "false", "0", "off"):
            with self.subTest(value=value), switch(value):
                self.assertFalse(mp.analysis_response_grammar_enabled())
                config = gemini_config()
                self.assertNotIn("response_json_schema", config)
                self.assertEqual(config["response_mime_type"], "application/json")
                first, schema_part = config["system_instruction"].split("\n\n", 1)
                self.assertEqual(first, "RULES")
                self.assertEqual(
                    json.loads(schema_part.split("\n", 1)[1]), ANALYSIS_PROVIDER_RESPONSE_SCHEMA)

    def test_on_the_analysis_schema_is_the_response_grammar(self) -> None:
        for value in ("true", "1", "on", " TRUE "):
            with self.subTest(value=value), switch(value):
                self.assertTrue(mp.analysis_response_grammar_enabled())
                config = gemini_config()
                self.assertEqual(config["response_mime_type"], "application/json")
                self.assertEqual(
                    config["response_json_schema"], mp._any_of(ANALYSIS_PROVIDER_RESPONSE_SCHEMA))
                # The schema is not also sent as an instruction.
                self.assertEqual(config["system_instruction"], "RULES")

    def test_on_other_roles_are_unchanged(self) -> None:
        with switch("true"):
            config = gemini_config(role="translation")
        self.assertNotIn("response_json_schema", config)
        self.assertIn("\n\n", config["system_instruction"])

    def test_a_small_schema_is_a_grammar_either_way(self) -> None:
        for value in ("false", "true"):
            with self.subTest(value=value), switch(value):
                fake, _ = call("google", "gemini-3.8-flash", SMALL)
                self.assertEqual(fake.requests[0]["config"]["response_json_schema"], mp._any_of(SMALL))

    def test_other_providers_are_unchanged(self) -> None:
        for provider, model in (("anthropic", "claude-opus-5-5"), ("openai", "gpt-6.1-sol")):
            with self.subTest(provider=provider):
                with switch("false"):
                    off, _ = call(provider, model, ANALYSIS_PROVIDER_RESPONSE_SCHEMA)
                with switch("true"):
                    on, _ = call(provider, model, ANALYSIS_PROVIDER_RESPONSE_SCHEMA)
                self.assertEqual(off.requests, on.requests)

    def test_the_setting_is_in_the_table_and_off_by_default(self) -> None:
        self.assertEqual(BY_NAME[SETTING].default, "false")
        self.assertIn("실험", BY_NAME[SETTING].meaning)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
