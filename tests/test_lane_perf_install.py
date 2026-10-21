"""Imports used at server boot must be declared without the test extra."""

from pathlib import Path
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[1]


class RuntimeInstallTests(unittest.TestCase):
    def test_httpx_is_an_explicit_runtime_dependency_in_both_manifests(self) -> None:
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())
        self.assertIn("httpx>=0.27", project["project"]["dependencies"])
        self.assertIn("httpx>=0.27", (ROOT / "requirements.txt").read_text().splitlines())

    def test_ci_imports_server_after_install_without_test_extra_in_clean_venv(self) -> None:
        workflow = (ROOT / ".github/workflows/ci.yml").read_text()
        self.assertIn('python -m venv "$RUNNER_TEMP/runtime-venv"', workflow)
        self.assertIn('"$RUNNER_TEMP/runtime-venv/bin/python" -m pip install .', workflow)
        self.assertIn('"$RUNNER_TEMP/runtime-venv/bin/python" -c "import voiney_lab.server"', workflow)
