"""An unset model role is "설정 필요" (lane AN, decision 1).

Human decision 2026-10-13. Lane CL left ``model_providers`` falling back to
xAI and grok models for a role whose settings are empty, so such a role
failed closed only when ``XAI_API_KEY`` was missing. Now an empty provider
setting means the role is not configured, whatever keys are set.
"""

from __future__ import annotations

import unittest
from unittest import mock

from voiney_lab import model_providers
from voiney_lab.model_providers import (
    DEFAULT_MODELS,
    ROLE_SETTINGS,
    ROLES,
    RoleModel,
    chat_client,
)

EVERY_KEY = {
    "XAI_API_KEY": "x-key",
    "ANTHROPIC_API_KEY": "a-key",
    "OPENAI_API_KEY": "o-key",
    "GEMINI_API_KEY": "g-key",
}


class AnUnsetRoleIsNotConfiguredTests(unittest.TestCase):
    def test_no_role_has_a_default_model(self):
        self.assertEqual(dict(DEFAULT_MODELS), {role: None for role in ROLES})

    def test_an_empty_provider_setting_is_no_provider_whatever_keys_are_set(self):
        for role in ROLES:
            with self.subTest(role=role):
                selected = RoleModel.from_environment(role, dict(EVERY_KEY))
                self.assertIsNone(selected.provider)
                self.assertIsNone(selected.model)
                self.assertFalse(selected.configured)
                self.assertFalse(selected.has_key(dict(EVERY_KEY)))

    def test_a_client_for_an_unset_role_is_refused_with_the_setting_to_fill(self):
        for role in ROLES:
            with self.subTest(role=role):
                selected = RoleModel.from_environment(role, dict(EVERY_KEY))
                with self.assertRaises(model_providers.RoleNotConfiguredError) as caught:
                    chat_client(selected, environment=dict(EVERY_KEY))
                self.assertEqual(
                    str(caught.exception), f"{ROLE_SETTINGS[role][0]} 설정 필요"
                )
                for key in EVERY_KEY.values():
                    self.assertNotIn(key, str(caught.exception))

    def test_a_set_provider_still_reads_its_key_and_model(self):
        environment = {
            **EVERY_KEY,
            "VOINEY_LAB_TRANSLATION_PROVIDER": "xai",
            "VOINEY_LAB_TRANSLATION_MODEL": "grok-4.6",
        }
        selected = RoleModel.from_environment("translation", environment)
        self.assertEqual((selected.provider, selected.model), ("xai", "grok-4.6"))
        self.assertTrue(selected.configured)
        self.assertTrue(selected.has_key(environment))
        self.assertFalse(selected.has_key({"VOINEY_LAB_TRANSLATION_PROVIDER": "xai"}))

    def test_the_server_role_client_refuses_an_unset_role(self):
        from voiney_lab import server

        environment = {
            **EVERY_KEY,
            "VOINEY_LAB_ANALYSIS_PROVIDER": "",
            "VOINEY_LAB_TRANSLATION_PROVIDER": "",
        }
        with mock.patch.dict("os.environ", environment):
            for role in ("analysis", "translation"):
                with self.subTest(role=role):
                    with self.assertRaises(model_providers.RoleNotConfiguredError):
                        server._role_client(RoleModel.from_environment(role))

if __name__ == "__main__":
    unittest.main()
