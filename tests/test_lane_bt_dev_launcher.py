"""``scripts/run_dev.sh`` starts the server when the fixture will not load (lane BT).

Decision 3 of lane BT (2026-10-09). From 2026-10-08 one refused fixture load
kept the development server down: the load ran under ``set -euo pipefail``
and uvicorn was never reached. The server already leaves an unloaded fixture
unrunnable on its own, so the launcher now says so, loudly, and starts;
``--bootstrap-only`` still ends with a non-zero code.

The launcher runs from a temporary copy, as in
``tests/test_launcher_settings_win.py``, so nothing under the repository's
``data/runtime`` is read or written.
"""

from __future__ import annotations

import shutil
import sys
import unittest
from pathlib import Path

from tests.test_launcher_settings_win import SERVE_MARKER, _LauncherCopy
from tests.test_protocol_catalog import write_text_pdf

ROOT = Path(__file__).resolve().parents[1]
WARNING = "[WARN] 개발 픽스처를 적재하지 못함 — 이 픽스처는 실행할 수 없음: "


class DevLauncherStartsWithoutTheFixtureTests(_LauncherCopy):
    """``scripts/run_dev.sh`` from a temporary copy (lane BT, decision 3).

    The launcher's PDF digest check is stood in by a ``sha256sum`` that names
    the expected digest, so no licensed PDF is needed to get past it; the
    stand-in PDF then fails the fixture's own check inside the load, which is
    the failure under test. The stand-in python refuses to serve, so reaching
    the server stage shows as its marker and exit code 97.
    """

    SCRIPT = "run_dev.sh"

    def setUp(self) -> None:
        super().setUp()
        fixtures = self.root / "data" / "fixtures" / "development_protocols"
        fixtures.mkdir(parents=True)
        for name in (
            "candidate_a_curated_analysis.json",
            "candidate_a_curated_analysis.provenance.json",
        ):
            shutil.copy2(ROOT / "data/fixtures/development_protocols" / name, fixtures)
        self.source_pdf = self.root / "stand-in.pdf"
        write_text_pdf(self.source_pdf, "Stand-in", title="Stand-in")
        expected = next(
            line.split('"')[1]
            for line in (ROOT / "scripts" / self.SCRIPT).read_text().splitlines()
            if line.startswith("EXPECTED_PDF_SHA256=")
        )
        bin_dir = self.root / ".venv" / "bin"
        sha256sum = bin_dir / "sha256sum"
        sha256sum.write_text(f'#!/bin/sh\necho "{expected}  $1"\n', encoding="utf-8")
        sha256sum.chmod(0o755)

    def _stand_in_python(self, *, loader_exit: int) -> None:
        """A python whose fixture loader dies before it can say why."""

        python = self.root / ".venv" / "bin" / "python"
        python.write_text(
            "#!/bin/sh\n"
            f'case " $* " in *" uvicorn "*) echo "{SERVE_MARKER}" >&2; exit 97;; esac\n'
            f'case " $* " in *" -B - false "*) cat >/dev/null; exit {loader_exit};; esac\n'
            f'exec "{sys.executable}" "$@"\n',
            encoding="utf-8",
        )

    def test_e_a_fixture_that_fails_to_load_still_reaches_the_server(self) -> None:
        result = self._run(VOINEY_LAB_CANDIDATE_A_SOURCE_PDF=str(self.source_pdf))

        self.assertEqual(result.returncode, 97, result.stdout + result.stderr)
        self.assertIn(SERVE_MARKER, result.stderr)
        warning = next(
            line for line in result.stdout.splitlines() if line.startswith(WARNING)
        )
        self.assertRegex(warning[len(WARNING):], r"^[A-Za-z_][A-Za-z0-9_]*Error$")
        self.assertNotIn("[OK] LOAD_OK", result.stdout)
        self.assertLess(
            result.stdout.index(WARNING), result.stdout.index("=== Starting Voiney Lab ===")
        )

    def test_e_a_loader_that_dies_still_reaches_the_server(self) -> None:
        self._stand_in_python(loader_exit=70)
        result = self._run(VOINEY_LAB_CANDIDATE_A_SOURCE_PDF=str(self.source_pdf))

        self.assertEqual(result.returncode, 97, result.stdout + result.stderr)
        self.assertIn(WARNING, result.stdout)
        self.assertIn("70", result.stdout.split(WARNING, 1)[1].splitlines()[0])

    def test_e_bootstrap_only_still_fails(self) -> None:
        result = self._run(
            "--bootstrap-only",
            VOINEY_LAB_CANDIDATE_A_SOURCE_PDF=str(self.source_pdf),
        )

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotEqual(result.returncode, 97)
        self.assertNotIn(SERVE_MARKER, result.stderr)
        self.assertNotIn(WARNING, result.stdout)
        self.assertNotIn("bootstrap complete", result.stdout)
        self.assertIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
