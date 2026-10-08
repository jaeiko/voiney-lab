"""A person's settings win over the launchers', and xAI is optional (lane XO).

Decision 1 of 2026-10-05: scripts/run_dev.sh and scripts/run_pilot.sh keep
a value a person set in the shell or wrote in the repository .env; they name
no model, and the four features only xAI provides default to off.
Decision 2: one of those features switched on without XAI_API_KEY is refused
at start-up, by name; with all four off, and no role on xAI, the server and
both launchers start without an xAI key.

The launchers run from a temporary copy whose ROOT is a temp dir, with a
stand-in .venv that refuses to serve (as tests/test_pilot_safety.py does), so
nothing under the repository's data/runtime is read or written.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_pilot_safety import _document
from voiney_lab.configuration import (
    XAI_ONLY_FEATURES,
    XaiKeyRequiredError,
    launcher_defaults,
    refuse_xai_only_features_without_key,
)
from voiney_lab.document_store import ingest_manifest

ROOT = Path(__file__).resolve().parents[1]
NO_XAI_ROLES = {
    f"VOINEY_LAB_{role}_PROVIDER": "openai"
    for role in ("ROUTER", "ANSWER", "TRANSLATION", "ANALYSIS", "REPORT", "SUPPLEMENTAL")
}
SERVE_MARKER = "stand-in python: the launcher tried to serve"


def _clean_environment(**extra: str) -> dict[str, str]:
    """This process's environment without xAI, feature or role settings."""

    env = {
        key: value for key, value in os.environ.items()
        if key != "XAI_API_KEY"
        and key not in XAI_ONLY_FEATURES
        and not re.fullmatch(r"VOINEY_LAB_[A-Z]+_(?:PROVIDER|MODEL|REASONING)", key)
        and key not in {
            "VOINEY_LAB_SUPPLEMENTAL_MODEL_KNOWLEDGE_ENABLED",
            "VOINEY_LAB_MULTI_BRAIN_ENABLED",
            "VOINEY_LAB_USAGE_SCOPE", "VOINEY_LAB_SAFETY_CATALOG",
        }
    }
    env.update({"PYTHONDONTWRITEBYTECODE": "1", "HOST": "127.0.0.1", "PORT": "0"})
    env.update(extra)
    return env


class LauncherDefaultsTests(unittest.TestCase):
    DEFAULTS = (("XO_TEST_A", "true"), ("XO_TEST_B", "false"), ("XO_TEST_C", "x"))

    def test_the_shell_and_the_dotenv_win_over_the_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            dotenv = Path(temp) / ".env"
            dotenv.write_text("XO_TEST_B=true\nXO_TEST_A=from-file\nOTHER=secret\n", encoding="utf-8")
            chosen = launcher_defaults(
                self.DEFAULTS, environment={"XO_TEST_A": ""}, dotenv_path=dotenv,
            )
        # A: set (empty) in the shell, left alone. B: the file's value.
        # C: nobody set it, the default. OTHER is never read out.
        self.assertEqual(chosen, (("XO_TEST_B", "true"), ("XO_TEST_C", "x")))

    def test_without_a_dotenv_every_unset_name_gets_its_default(self) -> None:
        chosen = launcher_defaults(self.DEFAULTS, environment={}, dotenv_path=Path("/nonexistent/.env"))
        self.assertEqual(chosen, self.DEFAULTS)


class XaiOnlyFeatureRefusalTests(unittest.TestCase):
    def test_each_feature_on_without_the_key_is_refused_by_name(self) -> None:
        self.assertEqual(set(XAI_ONLY_FEATURES), {
            "VOINEY_LAB_EXTERNAL_REFERENCES_ENABLED",
        })
        for name, label in XAI_ONLY_FEATURES.items():
            with self.subTest(name=name):
                with self.assertRaises(XaiKeyRequiredError) as caught:
                    refuse_xai_only_features_without_key({name: "true"})
                self.assertIn(name, str(caught.exception))
                self.assertIn(label, str(caught.exception))
                refuse_xai_only_features_without_key({name: "true", "XAI_API_KEY": "fake"})

    def test_all_off_starts_without_the_key(self) -> None:
        refuse_xai_only_features_without_key({})
        refuse_xai_only_features_without_key({name: "false" for name in XAI_ONLY_FEATURES})

    def _import_server(self, **extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-c", "import voiney_lab.server"],
            cwd=ROOT, env=_clean_environment(**NO_XAI_ROLES, **extra),
            capture_output=True, text=True, timeout=120,
        )

    def test_the_server_starts_without_an_xai_key(self) -> None:
        result = self._import_server()
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])

    def test_the_server_refuses_an_xai_only_feature_without_the_key(self) -> None:
        result = self._import_server(VOINEY_LAB_EXTERNAL_REFERENCES_ENABLED="true")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("XaiKeyRequiredError", result.stderr)
        self.assertIn("외부 근거 웹 검색(VOINEY_LAB_EXTERNAL_REFERENCES_ENABLED)", result.stderr)


class _LauncherCopy(unittest.TestCase):
    SCRIPT = ""

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        (self.root / "scripts").mkdir()
        shutil.copy2(ROOT / "scripts" / self.SCRIPT, self.root / "scripts" / self.SCRIPT)
        bin_dir = self.root / ".venv" / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "activate").write_text(f'export PATH="{bin_dir}:$PATH"\n', encoding="utf-8")
        python = bin_dir / "python"
        python.write_text(
            "#!/bin/sh\n"
            f'case " $* " in *" uvicorn "*) echo "{SERVE_MARKER}" >&2; exit 97;; esac\n'
            f'exec "{sys.executable}" "$@"\n',
            encoding="utf-8",
        )
        python.chmod(0o755)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def _dotenv(self, text: str) -> None:
        (self.root / ".env").write_text(text, encoding="utf-8")

    def _run(self, *args: str, **extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(self.root / "scripts" / self.SCRIPT), *args],
            cwd=self.root, env=_clean_environment(**extra), text=True,
            capture_output=True, timeout=120, check=False,
        )


class DevLauncherTests(_LauncherCopy):
    SCRIPT = "run_dev.sh"

    def test_it_names_no_model(self) -> None:
        text = (ROOT / "scripts" / self.SCRIPT).read_text(encoding="utf-8")
        exported = re.findall(r'^export (VOINEY_LAB_\w*_MODEL)="([^"]*)"', text, re.M)
        # Lane PA decision 2 (2026-10-06): the analysis model is no longer
        # cleared, so the launcher exports no model name at all.
        self.assertEqual(exported, [])
        self.assertNotIn("grok-", text)

    def test_with_nothing_set_it_starts_without_an_xai_key(self) -> None:
        result = self._run("--check-only")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for line in (
            "authoritative_web_search: disabled",
            "[OK] --check-only",
        ):
            self.assertIn(line, result.stdout)
        self.assertFalse((self.root / "data").exists(), "data/runtime was touched")

    def test_the_dotenv_and_the_shell_win(self) -> None:
        self._dotenv(
            "VOINEY_LAB_SUPPLEMENTAL_PROVIDER=openai\n"
            "VOINEY_LAB_SUPPLEMENTAL_MODEL=gpt-test\n"
            "VOINEY_LAB_ANSWER_PROVIDER=anthropic\n"
            "VOINEY_LAB_ANSWER_MODEL=claude-test\n"
            "VOINEY_LAB_SUPPLEMENTAL_MODEL_KNOWLEDGE_ENABLED=false\n"
            "VOINEY_LAB_MULTI_BRAIN_ENABLED=true\n"
        )
        result = self._run("--check-only", VOINEY_LAB_MULTI_BRAIN_ENABLED="false")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("supplemental_role: openai gpt-test", result.stdout)
        self.assertIn("answer_role: anthropic claude-test", result.stdout)
        self.assertIn("supplemental_model_knowledge: disabled", result.stdout)
        self.assertIn("hybrid_multi_brain: disabled", result.stdout)

    def test_an_xai_only_feature_in_the_dotenv_needs_the_key(self) -> None:
        self._dotenv("VOINEY_LAB_EXTERNAL_REFERENCES_ENABLED=true\n")
        result = self._run("--check-only")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("authoritative_web_search: enabled", result.stdout)
        self.assertIn("외부 근거 웹 검색(VOINEY_LAB_EXTERNAL_REFERENCES_ENABLED)", result.stderr)
        self.assertNotIn("[OK] --check-only", result.stdout)
        result = self._run("--check-only", XAI_API_KEY="fake-key")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class PilotLauncherSettingsTests(_LauncherCopy):
    SCRIPT = "run_pilot.sh"

    def setUp(self) -> None:
        super().setUp()
        catalog = self.root / "data" / "runtime" / "pilot" / "approved_safety_catalog.sqlite"
        catalog.parent.mkdir(parents=True)
        ingest_manifest({"documents": [_document(
            "REF-SOP-SPILL", "facility_sop", "Solvent spill response",
            "Contain the spill with absorbent pads and notify the lab manager.",
            topic="spill", usage_scope="reference_only",
        )]}, catalog)

    def test_the_dotenv_turns_a_feature_on_and_the_banner_says_so(self) -> None:
        self._dotenv("VOINEY_LAB_SUPPLEMENTAL_MODEL_KNOWLEDGE_ENABLED=true\n")
        result = self._run("--check-only")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertRegex(result.stdout, r"supplemental_model_knowledge:\s+enabled")
        self.assertRegex(result.stdout, r"external_references:\s+disabled")

    def test_an_xai_only_feature_without_the_key_is_refused_by_name(self) -> None:
        self._dotenv("VOINEY_LAB_EXTERNAL_REFERENCES_ENABLED=true\n")
        result = self._run("--check-only")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("VOINEY_LAB_EXTERNAL_REFERENCES_ENABLED", result.stderr)
        self.assertIn("[ERROR] Refusing to start the pilot.", result.stdout)
        self.assertNotIn(SERVE_MARKER, result.stderr)

    def test_with_nothing_set_it_starts_without_an_xai_key(self) -> None:
        result = self._run("--check-only")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertRegex(result.stdout, r"external_references:\s+disabled")


if __name__ == "__main__":
    unittest.main()
