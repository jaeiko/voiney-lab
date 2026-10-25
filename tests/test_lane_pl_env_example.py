"""Lane PL: ``.env.example`` is current -- complete, safe to copy, and without stale words.

The example had fallen behind: 57 of the table's 136 settings were missing, it
named a Moss setting nothing reads, described a code fallback that lane PL
removed, set two keyed features on with no key (a copy of it was refused at the
first connection), put a guessable admin token and a folder outside
``data/runtime`` where the server would refuse it. These tests keep it from
drifting again. Nothing here calls a provider or reads a real ``.env``.
"""

from __future__ import annotations

import os
import re
import unittest
from pathlib import Path
from unittest.mock import patch

from dotenv import dotenv_values

from voiney_lab.setting_names import BY_NAME, SETTINGS

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / ".env.example"

#: Settings the example does not list, each with the reason.
NOT_LISTED = {
    # Set by the shell, CI or pytest, not by a .env.
    "HOST": "the launchers read it from the shell",
    "PORT": "the launchers read it from the shell",
    "VIRTUAL_ENV": "set by the Python venv",
    "RUNNER_TEMP": "set by GitHub Actions",
    "PYTEST_VERSION": "set by pytest",
    "VOINEY_LAB_PLAYWRIGHT_APP_PORT": "read by the Playwright config from the shell",
    # An alias or an older name of a setting that is listed.
    "VOINEY_LAB_EXPERIMENT_REPORTS_DATABASE": "alias of VOINEY_LAB_EXPERIMENT_REPORT_DB",
    "VOINEY_LAB_SAFETY_USAGE_SCOPE": "alias of VOINEY_LAB_USAGE_SCOPE",
    "GOOGLE_GENAI_USE_VERTEXAI": "older name of GOOGLE_GENAI_USE_ENTERPRISE",
    # Follow another setting unless set; writing a value would stop following it.
    "VOINEY_LAB_ANSWER_BRAIN_ENABLED": "follows VOINEY_LAB_MULTI_BRAIN_ENABLED; an empty value would turn it off",
    "VOINEY_LAB_SOURCE_BRAIN_ENABLED": "follows VOINEY_LAB_MULTI_BRAIN_ENABLED; an empty value would turn it off",
    "VOINEY_LAB_VISUAL_BRAIN_ENABLED": "follows VOINEY_LAB_MULTI_BRAIN_ENABLED; an empty value would turn it off",
    # Fixed by scripts/run_dev.sh (and refused empty-handed by the pilot).
    "VOINEY_LAB_CURATED_PROTOCOL_FIXTURE": "the development launcher sets it; the pilot needs it empty",
    "VOINEY_LAB_CURATED_PROTOCOL_PROVENANCE": "the development launcher sets it; the pilot needs it empty",
    "VOINEY_LAB_CURATED_PROTOCOL_SOURCE_PDF": "the development launcher sets it; the pilot needs it empty",
    "VOINEY_LAB_CANDIDATE_A_SOURCE_PDF": "read by scripts/run_dev.sh from the shell only",
    # Another program's own name (lane AN, FM).
    "FONTCONFIG_FILE": "fontconfig's; the server sets it only for one LibreOffice run",
}
SECRET_NAME = re.compile(r"(API_KEY|SECRET|TOKEN|PASSWORD)$")


def example_values() -> dict[str, str]:
    return {name: value or "" for name, value in dotenv_values(EXAMPLE).items()}


class ListsEverySettingTests(unittest.TestCase):
    def test_every_table_setting_is_listed_unless_the_reason_is_written_here(self):
        listed = set(example_values())
        missing = sorted(
            setting.name for setting in SETTINGS
            if setting.name not in listed and setting.name not in NOT_LISTED
        )
        self.assertEqual(missing, [], "settings the example does not list (add them, or give the reason in NOT_LISTED)")

    def test_the_reasons_name_real_settings_and_none_of_them_is_listed(self):
        listed = set(example_values())
        self.assertEqual(sorted(set(NOT_LISTED) - set(BY_NAME)), [])
        self.assertEqual(sorted(set(NOT_LISTED) & listed), [])

    def test_it_lists_nothing_the_table_does_not_know(self):
        self.assertEqual(sorted(set(example_values()) - set(BY_NAME)), [])


class SafeToCopyTests(unittest.TestCase):
    def test_no_key_secret_or_token_has_a_value(self):
        filled = sorted(
            name for name, value in example_values().items()
            if SECRET_NAME.search(name) and value.strip()
        )
        self.assertEqual(filled, [])

    def test_no_value_looks_like_a_credential(self):
        suspicious = sorted(
            name for name, value in example_values().items()
            if re.search(r"(sk-[A-Za-z0-9]{16,}|AIza[0-9A-Za-z_-]{20,}|xai-[A-Za-z0-9]{16,}|replace-with)", value)
        )
        self.assertEqual(suspicious, [])

    def test_copied_as_it_is_every_setting_parses_without_a_key(self):
        """A copy of the example, no key written, builds each settings object.

        The server refuses a feature switched on without its key and a path
        outside its folder; a template that did either would fail at the first
        connection. (``server_config()`` is left out: the safety catalog path is
        a placeholder to replace, by design.)
        """

        from voiney_lab import server as server_module
        from voiney_lab.configuration import VoiceVadSettings
        from voiney_lab.drawn_diagrams import DrawnDiagramSettings
        from voiney_lab.experiment_reports import ExperimentReportSettings, ReportWriterSettings
        from voiney_lab.external_references import SupplementalKnowledgeSettings
        from voiney_lab.identity import OidcSettings
        from voiney_lab.llm_router import LlmRouterSettings
        from voiney_lab.model_providers import ROLES, RoleModel
        from voiney_lab.multi_brain import MultiBrainSettings
        from voiney_lab.voice_providers import SttProviderSettings, TtsProviderSettings
        from voiney_lab.web_explanations import WebExplanationSettings

        builders = {
            "VoiceVadSettings": VoiceVadSettings.from_environment,
            "ExperimentReportSettings": ExperimentReportSettings.from_environment,
            "ReportWriterSettings": ReportWriterSettings.from_environment,
            "SupplementalKnowledgeSettings": SupplementalKnowledgeSettings.from_environment,
            "MultiBrainSettings": MultiBrainSettings.from_environment,
            "LlmRouterSettings": LlmRouterSettings.from_environment,
            "WebExplanationSettings": WebExplanationSettings.from_environment,
            "DrawnDiagramSettings": DrawnDiagramSettings.from_environment,
            "SttProviderSettings": SttProviderSettings.from_environment,
            "TtsProviderSettings": TtsProviderSettings.from_environment,
            "workspace": server_module._workspace_settings,
            "protocol store": server_module._protocol_store_settings,
            "STT diagnostics": server_module.SttDiagnosticSettings.from_environment,
            "OIDC": OidcSettings.from_environment,
        }
        builders.update({f"role {role}": (lambda role=role: RoleModel.from_environment(role)) for role in ROLES})
        failures = {}
        with patch.dict(os.environ, example_values(), clear=True):
            for name, build in builders.items():
                try:
                    build()
                except Exception as exc:  # noqa: BLE001 - reported by name below
                    failures[name] = f"{type(exc).__name__}: {exc}"
        self.assertEqual(failures, {})

    def test_the_admin_metrics_stay_closed_and_the_keyed_features_start_off(self):
        values = example_values()
        self.assertEqual(values["VOINEY_LAB_ADMIN_TOKEN"], "")
        for name in (
            "VOINEY_LAB_WEB_EXPLANATIONS_ENABLED", "VOINEY_LAB_DRAWN_DIAGRAMS_ENABLED",
            "VOINEY_LAB_SUPPLEMENTAL_MODEL_KNOWLEDGE_ENABLED",
            "VOINEY_LAB_MULTI_BRAIN_ENABLED", "VOINEY_LAB_LLM_ROUTER_ENABLED",
            "VOINEY_LAB_STT_DIAGNOSTICS_ENABLED",
        ):
            with self.subTest(name=name):
                self.assertEqual(values[name].strip().casefold(), "false")

    def test_every_role_the_table_says_to_write_is_written(self):
        values = example_values()
        for role in ("ROUTER", "ANSWER", "TRANSLATION", "ANALYSIS", "REPORT", "SUPPLEMENTAL"):
            for part in ("PROVIDER", "MODEL"):
                name = f"VOINEY_LAB_{role}_{part}"
                with self.subTest(name=name):
                    self.assertTrue(values[name].strip(), f"{name} must be written down")
        for name in ("VOINEY_LAB_STT_PROVIDER", "VOINEY_LAB_TTS_PROVIDER"):
            with self.subTest(name=name):
                self.assertTrue(values[name].strip(), f"{name} must be written down")


class NoStaleWordsTests(unittest.TestCase):
    def test_it_describes_no_fallback_the_code_no_longer_has_and_no_setting_nothing_reads(self):
        text = EXAMPLE.read_text(encoding="utf-8")
        for stale in (
            # Lane KNOW (2026-10-24) added MOSS_PROJECT_ID/KEY for the public
            # knowledge search, so "Moss" itself is a current word again; the
            # deleted setting and module stay forbidden.
            "old xai default", "old fallback", "code's old", "MOSS_ENABLED", "moss_retrieval",
            "코드가 읽지 않음",
            # The web reference search was deleted in lane DOC3.
            "외부 근거", "EXTERNAL_REFERENCE", "web reference",
        ):
            with self.subTest(stale=stale):
                self.assertNotIn(stale, text)

    def test_it_names_the_new_usage_scope(self):
        self.assertIn("source_protocol_only", EXAMPLE.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
