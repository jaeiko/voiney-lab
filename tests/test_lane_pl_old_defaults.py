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
import re
import unittest
from pathlib import Path
from unittest.mock import patch

from voiney_lab import voice_providers
from voiney_lab.configuration import ConfigurationError
from voiney_lab.experiment_reports import ReportWriterSettings
from voiney_lab.external_references import SupplementalKnowledgeSettings

SRC = Path(__file__).resolve().parents[1] / "src" / "voiney_lab"
PROVIDER = "VOINEY_LAB_SUPPLEMENTAL_PROVIDER"
MODEL = "VOINEY_LAB_SUPPLEMENTAL_MODEL"


def refusal(test: unittest.TestCase, settings_class, environment: dict[str, str]) -> str:
    """The message the settings class refuses ``environment`` with."""

    with patch.dict(os.environ, environment, clear=True):
        with test.assertRaises(ValueError) as caught:
            settings_class.from_environment()
    return str(caught.exception)


class SupplementalDefaultsTests(unittest.TestCase):
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

    def test_it_stays_off_without_a_word_when_its_switch_is_off(self):
        with patch.dict(os.environ, {}, clear=True):
            supplemental = SupplementalKnowledgeSettings.from_environment()
        self.assertFalse(supplemental.enabled)
        self.assertEqual(supplemental.model, "")


REPORT_PROVIDER = "VOINEY_LAB_REPORT_PROVIDER"
REPORT_MODEL = "VOINEY_LAB_REPORT_MODEL"


class ReportWriterDefaultsTests(unittest.TestCase):
    """The report's prose writer is on only for a role somebody set."""

    def settings(self, environment: dict[str, str]) -> ReportWriterSettings:
        with patch.dict(os.environ, environment, clear=True):
            return ReportWriterSettings.from_environment()

    def test_without_a_provider_and_a_model_the_writer_stays_off_and_says_why(self):
        for given in (
            {},
            {REPORT_PROVIDER: "google"},
            {REPORT_MODEL: "gemini-test"},
            {MODEL: "gpt-test"},
            {REPORT_PROVIDER: "google", REPORT_MODEL: "  "},
        ):
            with self.subTest(given=sorted(given)):
                with self.assertLogs("voiney_lab.experiment_reports", "WARNING") as logged:
                    settings = self.settings(given)
                self.assertFalse(settings.enabled)
                self.assertIn(REPORT_PROVIDER, logged.output[0])
                self.assertIn(REPORT_MODEL, logged.output[0])
                self.assertIn("설정 필요", logged.output[0])

    def test_a_provider_and_a_model_turn_the_writer_on(self):
        settings = self.settings({REPORT_PROVIDER: "google", REPORT_MODEL: "gemini-test"})
        self.assertEqual((settings.enabled, settings.model), (True, "gemini-test"))

    def test_the_report_model_still_falls_back_to_the_supplemental_model(self):
        settings = self.settings({REPORT_PROVIDER: "openai", MODEL: "gpt-test"})
        self.assertEqual((settings.enabled, settings.model), (True, "gpt-test"))

    def test_switched_off_on_purpose_it_is_off_without_a_warning(self):
        with self.assertNoLogs("voiney_lab.experiment_reports", "WARNING"):
            settings = self.settings({"VOINEY_LAB_REPORT_WRITER_ENABLED": "false"})
        self.assertFalse(settings.enabled)

    def test_the_server_builds_no_writer_for_an_unset_report_role(self):
        from voiney_lab.server import _report_writer_brain

        # An xAI key alone no longer sends the report prose to xAI's grok.
        for given in ({}, {"XAI_API_KEY": "fake-key"}):
            with self.subTest(given=sorted(given)), patch.dict(
                os.environ, given, clear=True,
            ):
                self.assertIsNone(_report_writer_brain())
        with patch.dict(os.environ, {
            REPORT_PROVIDER: "xai", REPORT_MODEL: "model-test", "XAI_API_KEY": "fake-key",
        }, clear=True):
            brain = _report_writer_brain()
        self.assertIsNotNone(brain)
        self.assertEqual(brain.model, "model-test")


STT_PROVIDER = "VOINEY_LAB_STT_PROVIDER"
TTS_PROVIDER = "VOINEY_LAB_TTS_PROVIDER"
PCM = b"\0\0" * 1600


class VoiceProviderDefaultsTests(unittest.TestCase):
    """An unset speech provider is "설정 필요", not xai."""

    def test_an_unset_provider_is_no_provider(self):
        stt = voice_providers.SttProviderSettings.from_environment({})
        tts = voice_providers.TtsProviderSettings.from_environment({})
        self.assertEqual((stt.provider, stt.model), ("", ""))
        self.assertEqual((tts.provider, tts.model, tts.voice), ("", "", ""))
        # The classes carry no xai default either.
        self.assertEqual(voice_providers.SttProviderSettings().provider, "")
        self.assertEqual(voice_providers.TtsProviderSettings().provider, "")

    def test_a_written_provider_keeps_its_own_defaults(self):
        stt = voice_providers.SttProviderSettings.from_environment({STT_PROVIDER: "xai"})
        tts = voice_providers.TtsProviderSettings.from_environment({TTS_PROVIDER: "XAI"})
        self.assertEqual((stt.provider, stt.model, stt.timeout_seconds), ("xai", "", 120.0))
        self.assertEqual((tts.provider, tts.model, tts.voice), ("xai", "", "leo"))

    def test_a_bad_provider_is_still_refused_by_name(self):
        for name, settings_class in (
            (STT_PROVIDER, voice_providers.SttProviderSettings),
            (TTS_PROVIDER, voice_providers.TtsProviderSettings),
        ):
            with self.subTest(name=name), self.assertRaisesRegex(ConfigurationError, name):
                settings_class.from_environment({name: "whisper"})

    def test_the_adapters_refuse_an_unset_provider_by_name(self):
        with self.assertRaisesRegex(ConfigurationError, rf"{STT_PROVIDER}.*설정 필요"):
            voice_providers.transcribe(PCM, voice_providers.SttProviderSettings())
        with self.assertRaisesRegex(ConfigurationError, rf"{TTS_PROVIDER}.*설정 필요"):
            voice_providers.synthesize("안녕하세요", "ko", voice_providers.TtsProviderSettings())

    def test_the_server_sends_no_request_for_an_unset_provider(self):
        from voiney_lab.server import synthesize, transcribe

        with patch.dict(os.environ, {"XAI_API_KEY": "fake-key"}, clear=True), patch(
            "voiney_lab.server.requests.post"
        ) as xai_request, patch("voiney_lab.voice_providers.requests.post") as other_request:
            with self.assertRaisesRegex(ConfigurationError, STT_PROVIDER):
                transcribe(PCM)
            with self.assertRaisesRegex(ConfigurationError, TTS_PROVIDER):
                synthesize("안녕하세요", "ko")
        xai_request.assert_not_called()
        other_request.assert_not_called()


class NoOldModelDefaultTests(unittest.TestCase):
    """The three files spell no xAI or grok model as a fallback value."""

    OLD_DEFAULT = re.compile(r"""["']grok-[0-9][^"']*["']""")

    def test_no_grok_model_name_is_left_as_a_default(self):
        found = []
        for name in ("external_references.py", "experiment_reports.py", "voice_providers.py"):
            for number, line in enumerate(
                (SRC / name).read_text(encoding="utf-8").splitlines(), 1,
            ):
                if self.OLD_DEFAULT.search(line):
                    found.append(f"{name}:{number}: {line.strip()}")
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()
