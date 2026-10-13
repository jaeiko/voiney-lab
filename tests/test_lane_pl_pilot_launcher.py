"""Lane PL, stage 2a: scripts/run_pilot.sh starts without institution safety documents.

Human decision of 2026-10-13 ("B안"): with no approved safety catalog at the
fixed path, the pilot launcher starts as ``source_protocol_only`` -- the safety
statements of the uploaded PDF alone -- and says so, instead of refusing. A
catalog that is there keeps the checks it always had, and one that is there but
unusable is never taken for "no catalog".

The launcher runs from a temporary copy whose ROOT is a temp dir, with a
stand-in ``python`` that refuses to serve (as tests/test_pilot_safety.py does).
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_launcher_settings_win import SERVE_MARKER, _LauncherCopy
from tests.test_pilot_safety import _document, _moss_demo_documents
from voiney_lab.document_store import ingest_manifest

NOTICE = "기관 안전 문서 없음 · 원문 안전 주의만"


class _PilotCopy(_LauncherCopy):
    SCRIPT = "run_pilot.sh"

    def setUp(self) -> None:
        super().setUp()
        self.pilot_dir = self.root / "data" / "runtime" / "pilot"
        self.catalog = self.pilot_dir / "approved_safety_catalog.sqlite"

    def write_catalog(self, documents: list[dict]) -> None:
        self.catalog.parent.mkdir(parents=True, exist_ok=True)
        ingest_manifest({"documents": documents}, self.catalog)

    def reference_documents(self) -> list[dict]:
        return [_document(
            "REF-SOP-SPILL", "facility_sop", "Solvent spill response",
            "Contain the spill with absorbent pads and notify the lab manager.",
            topic="spill", usage_scope="reference_only",
        )]


class WithoutACatalogTests(_PilotCopy):
    def test_check_only_passes_and_says_what_is_missing(self):
        result = self._run("--check-only")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for line in (
            "USAGE_SCOPE    = source_protocol_only\n",
            f"SAFETY_SCOPE   = {NOTICE}\n",
            "[OK] --check-only",
        ):
            self.assertIn(line, result.stdout)
        # No catalog is shown as if there were one.
        self.assertIn("SAFETY_CATALOG = (none: ", result.stdout)
        self.assertNotIn("approved active", result.stdout)
        self.assertNotIn("demo documents", result.stdout)
        self.assertNotIn("[ERROR]", result.stdout)
        # --check-only touches nothing.
        self.assertFalse((self.root / "data").exists())

    def test_the_scope_and_the_catalog_are_fixed_here_not_by_the_environment(self):
        elsewhere = self.root / "elsewhere.sqlite"
        ingest_manifest({"documents": _moss_demo_documents()}, elsewhere)
        result = self._run(
            "--check-only",
            VOINEY_LAB_USAGE_SCOPE="demo", VOINEY_LAB_SAFETY_CATALOG=str(elsewhere),
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("[WARN] VOINEY_LAB_USAGE_SCOPE was set in the environment.", result.stdout)
        self.assertIn("[WARN] VOINEY_LAB_SAFETY_CATALOG was set in the environment.", result.stdout)
        self.assertIn("USAGE_SCOPE    = source_protocol_only\n", result.stdout)

    def test_the_server_is_handed_the_scope_and_no_catalog(self):
        # A stand-in python that writes the settings it was started with, then
        # refuses to serve.
        dump = self.root / "server-env.txt"
        python = self.root / ".venv" / "bin" / "python"
        python.write_text(
            "#!/bin/sh\n"
            f'case " $* " in *" uvicorn "*) env | grep "^VOINEY_LAB_\\(USAGE_SCOPE\\|SAFETY_CATALOG\\)=" > "{dump}"; '
            f'echo "{SERVE_MARKER}" >&2; exit 97;; esac\n'
            f'exec "{sys.executable}" "$@"\n',
            encoding="utf-8",
        )
        python.chmod(python.stat().st_mode | stat.S_IXUSR)
        result = self._run(
            VOINEY_LAB_SAFETY_CATALOG="/should/be/ignored.sqlite",
            VOINEY_LAB_USAGE_SCOPE="reference_only",
        )
        self.assertEqual(result.returncode, 97, result.stdout + result.stderr)
        self.assertIn(SERVE_MARKER, result.stderr)
        handed = dict(line.split("=", 1) for line in dump.read_text(encoding="utf-8").splitlines())
        self.assertEqual(handed, {
            "VOINEY_LAB_USAGE_SCOPE": "source_protocol_only",
            "VOINEY_LAB_SAFETY_CATALOG": "",
        })
        self.assertTrue(self.pilot_dir.is_dir())


class WithACatalogTests(_PilotCopy):
    def test_a_good_catalog_is_checked_as_it_always_was(self):
        self.write_catalog(self.reference_documents())
        result = self._run("--check-only")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"SAFETY_CATALOG = {self.catalog}\n", result.stdout)
        self.assertIn("USAGE_SCOPE    = reference_only\n", result.stdout)
        self.assertIn("approved active reference_only documents = 1\n", result.stdout)
        self.assertNotIn(NOTICE, result.stdout)
        self.assertNotIn("source_protocol_only", result.stdout)

    def test_a_catalog_that_is_there_but_unusable_is_never_taken_for_no_catalog(self):
        cases = {
            "a demo document in it": lambda: self.write_catalog(
                _moss_demo_documents() + self.reference_documents()),
            "no approved reference_only document": lambda: self.write_catalog([_document(
                "OPS-SOP", "facility_sop", "Spill response", "Notify the lab manager.",
                topic="spill")]),
            "not a catalog at all": lambda: (
                self.catalog.parent.mkdir(parents=True, exist_ok=True),
                self.catalog.write_bytes(b"this is not a sqlite database")),
            "a directory in its place": lambda: self.catalog.mkdir(parents=True),
            "a link that leads nowhere": lambda: (
                self.catalog.parent.mkdir(parents=True, exist_ok=True),
                os.symlink(self.root / "gone.sqlite", self.catalog)),
        }
        for name, make in cases.items():
            with self.subTest(case=name):
                if self.pilot_dir.exists():
                    import shutil
                    shutil.rmtree(self.pilot_dir)
                make()
                result = self._run("--check-only")
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("[ERROR] Refusing to start the pilot.", result.stdout)
                self.assertNotIn("[OK]", result.stdout)
                # The refusal names the way out: move the file away.
                self.assertIn("source_protocol_only", result.stdout)
                self.assertNotIn("USAGE_SCOPE    = source_protocol_only", result.stdout)


if __name__ == "__main__":
    unittest.main()
