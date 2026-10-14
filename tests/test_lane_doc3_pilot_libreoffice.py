"""Lane DOC3, decision 4: the pilot launcher warns about a missing LibreOffice, never refuses.

Human decision of 2026-10-14: without LibreOffice a Word (.docx) source cannot
be turned into a PDF, but a pilot that takes only PDFs does not need it, so
``scripts/run_pilot.sh`` prints a ``[WARN]`` and starts. The launcher runs from
a temporary copy with a stand-in ``.venv`` that refuses to serve (as in
``tests/test_launcher_settings_win.py``), so nothing under the repository's
``data/runtime`` is read or written and no server is started.
"""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

from tests.test_launcher_settings_win import SERVE_MARKER, _LauncherCopy, _clean_environment

WARNING = "[WARN] LibreOffice (soffice) was not found."
AVAILABLE = "word_sources(libreoffice):     available"


class PilotLibreOfficeTests(_LauncherCopy):
    SCRIPT = "run_pilot.sh"

    def _fake_soffice(self) -> Path:
        path = self.root / "fake-soffice"
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        path.chmod(0o755)
        return path

    def _run_without_converter_in_env(self, *args: str) -> subprocess.CompletedProcess:
        env = _clean_environment()
        env.pop("VOINEY_LAB_OFFICE_CONVERTER", None)
        return subprocess.run(
            ["bash", str(self.root / "scripts" / self.SCRIPT), *args],
            cwd=self.root, env=env, text=True, capture_output=True, timeout=120, check=False,
        )

    def test_without_libreoffice_check_only_warns_and_exits_zero(self) -> None:
        missing = self.root / "no-such-soffice"
        result = self._run("--check-only", VOINEY_LAB_OFFICE_CONVERTER=str(missing))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(WARNING, result.stdout)
        self.assertIn("word_sources(libreoffice):     unavailable", result.stdout)
        self.assertIn("apt-get install --no-install-recommends libreoffice-writer-nogui fonts-nanum",
                      result.stdout)
        self.assertNotIn("Refusing to start", result.stdout)
        self.assertIn("[OK] --check-only", result.stdout)

    def test_without_libreoffice_the_pilot_still_starts(self) -> None:
        missing = self.root / "no-such-soffice"
        result = self._run(VOINEY_LAB_OFFICE_CONVERTER=str(missing))
        # The stand-in python refuses only where the launcher runs uvicorn,
        # so reaching it means nothing before it stopped the start.
        self.assertEqual(result.returncode, 97, result.stdout + result.stderr)
        self.assertIn(SERVE_MARKER, result.stderr)
        self.assertIn(WARNING, result.stdout)
        self.assertNotIn("[ERROR]", result.stdout)

    def test_with_libreoffice_it_says_so_and_does_not_warn(self) -> None:
        soffice = self._fake_soffice()
        result = self._run("--check-only", VOINEY_LAB_OFFICE_CONVERTER=str(soffice))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"{AVAILABLE} ({soffice})", result.stdout)
        self.assertNotIn(WARNING, result.stdout)

    def test_the_dotenv_converter_is_the_one_the_banner_looks_for(self) -> None:
        soffice = self._fake_soffice()
        self._dotenv(f"VOINEY_LAB_OFFICE_CONVERTER={soffice}\n")
        result = self._run_without_converter_in_env("--check-only")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"{AVAILABLE} ({soffice})", result.stdout)
        self._dotenv(f"VOINEY_LAB_OFFICE_CONVERTER={self.root / 'gone'}\n")
        result = self._run_without_converter_in_env("--check-only")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(WARNING, result.stdout)

    def test_the_shells_converter_wins_over_the_dotenv(self) -> None:
        soffice = self._fake_soffice()
        self._dotenv(f"VOINEY_LAB_OFFICE_CONVERTER={self.root / 'gone'}\n")
        result = self._run("--check-only", VOINEY_LAB_OFFICE_CONVERTER=str(soffice))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"{AVAILABLE} ({soffice})", result.stdout)
        self.assertNotIn(WARNING, result.stdout)


if __name__ == "__main__":
    unittest.main()
