"""Lane PL, stage 1: a role nobody set no longer falls back to xAI and grok.

Lane CL (2026-10-10, human decision 2) made every role's provider and model
"설정 필요" in the setting table but left the code's old fallbacks in place, so
an unset role could still reach a provider nobody chose. These tests pin the
three files lane PL was allowed to change -- ``external_references.py``,
``experiment_reports.py`` and ``voice_providers.py`` -- to the table: an unset
provider or model is named in a refusal, or the feature stays off.

Nothing here calls a provider.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from voiney_lab.external_references import (
    ExternalReferenceSettings,
    SupplementalKnowledgeSettings,
)

PROVIDER = "VOINEY_LAB_SUPPLEMENTAL_PROVIDER"
MODEL = "VOINEY_LAB_SUPPLEMENTAL_MODEL"


def refusal(test: unittest.TestCase, settings_class, environment: dict[str, str]) -> str:
    """The message the settings class refuses ``environment`` with."""

    with patch.dict(os.environ, environment, clear=True):
        with test.assertRaises(ValueError) as caught:
            settings_class.from_environment()
    return str(caught.exception)


class ExternalReferenceDefaultsTests(unittest.TestCase):
    def test_the_web_reference_search_needs_the_provider_and_the_model_written_down(self):
        base = {"VOINEY_LAB_EXTERNAL_REFERENCES_ENABLED": "true"}
        for given, missing in (
            ({}, (PROVIDER, MODEL)),
            ({MODEL: "grok-test"}, (PROVIDER,)),
            ({PROVIDER: "xai"}, (MODEL,)),
        ):
            with self.subTest(given=sorted(given)):
                message = refusal(self, ExternalReferenceSettings, {**base, **given})
                self.assertIn("설정 필요", message)
                for name in missing:
                    self.assertIn(name, message)
                for name in {PROVIDER, MODEL} - set(missing):
                    self.assertNotIn(name, message)

    def test_the_web_reference_search_takes_the_model_that_was_written_down(self):
        with patch.dict(os.environ, {
            "VOINEY_LAB_EXTERNAL_REFERENCES_ENABLED": "true",
            PROVIDER: "xai", MODEL: "grok-test",
        }, clear=True):
            settings = ExternalReferenceSettings.from_environment()
        self.assertEqual(settings.model, "grok-test")

    def test_an_other_provider_is_still_refused_by_name(self):
        with patch.dict(os.environ, {
            "VOINEY_LAB_EXTERNAL_REFERENCES_ENABLED": "true",
            PROVIDER: "openai", MODEL: "some-model",
        }, clear=True), self.assertRaisesRegex(ValueError, "SUPPLEMENTAL_PROVIDER=xai"):
            ExternalReferenceSettings.from_environment()

    def test_the_general_explanation_needs_the_provider_and_the_model_written_down(self):
        base = {"VOINEY_LAB_SUPPLEMENTAL_MODEL_KNOWLEDGE_ENABLED": "true"}
        for given, missing in (
            ({}, (PROVIDER, MODEL)),
            ({MODEL: "gpt-test"}, (PROVIDER,)),
            ({PROVIDER: "openai"}, (MODEL,)),
            ({PROVIDER: "openai", MODEL: "  "}, (MODEL,)),
        ):
            with self.subTest(given=sorted(given)):
                message = refusal(self, SupplementalKnowledgeSettings, {**base, **given})
                self.assertIn("설정 필요", message)
                for name in missing:
                    self.assertIn(name, message)

    def test_the_general_explanation_keeps_working_when_it_is_set_up(self):
        with patch.dict(os.environ, {
            "VOINEY_LAB_SUPPLEMENTAL_MODEL_KNOWLEDGE_ENABLED": "true",
            PROVIDER: "openai", MODEL: "gpt-test",
        }, clear=True):
            settings = SupplementalKnowledgeSettings.from_environment()
        self.assertEqual((settings.enabled, settings.model), (True, "gpt-test"))

    def test_both_stay_off_without_a_word_when_their_switch_is_off(self):
        with patch.dict(os.environ, {}, clear=True):
            external = ExternalReferenceSettings.from_environment()
            supplemental = SupplementalKnowledgeSettings.from_environment()
        self.assertFalse(external.enabled)
        self.assertFalse(supplemental.enabled)
        self.assertEqual((external.model, supplemental.model), ("", ""))


if __name__ == "__main__":
    unittest.main()
