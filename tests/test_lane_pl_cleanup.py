"""Lane PL, stage 1: what lane CL left behind of the deleted Moss connection.

Lane CL (2026-10-10) deleted ``moss_retrieval.py`` and everything that read a
Moss setting. It left, for lane PL: the launchers' ``VOINEY_LAB_MOSS_ENABLED``
exports (nothing reads the name), Moss mentions in ``safety_pack.py`` and
``retrieval.py``, ``retrieval.search_safety_documents`` (nothing in ``src``
calls it) and ``scripts/setup_moss_demo.py``. These tests pin them gone.

``VOINEY_LAB_MOSS_ENABLED`` itself stays in the setting table and in
``scripts/collect_chunks.sh``: the table may change only by lane PL's new
usage-scope line, and ``tests/test_setting_names.py`` wants every table name
spelled by some script.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from voiney_lab import retrieval

from tests.test_launcher_settings_win import _LauncherCopy
from tests.test_pilot_safety import _document
from voiney_lab.document_store import ingest_manifest

ROOT = Path(__file__).resolve().parents[1]
MOSS = re.compile(r"moss", re.IGNORECASE)


class LauncherMossTests(unittest.TestCase):
    def test_no_run_launcher_exports_or_prints_a_moss_flag(self):
        found = []
        for script in sorted((ROOT / "scripts").glob("run_*.sh")):
            for number, line in enumerate(script.read_text(encoding="utf-8").splitlines(), 1):
                if MOSS.search(line):
                    found.append(f"{script.name}:{number}: {line.strip()}")
        self.assertEqual(found, [])


class ModuleMossTests(unittest.TestCase):
    def test_safety_pack_and_retrieval_name_no_moss(self):
        found = []
        for name in ("safety_pack.py", "retrieval.py"):
            for number, line in enumerate(
                (ROOT / "src" / "voiney_lab" / name).read_text(encoding="utf-8").splitlines(), 1,
            ):
                if MOSS.search(line):
                    found.append(f"{name}:{number}: {line.strip()}")
        self.assertEqual(found, [])


class SetupScriptTests(unittest.TestCase):
    def test_the_moss_demo_catalog_script_is_gone_and_the_manifest_it_read_stays(self):
        # The script built a catalog for the Moss retrieval demo. The manifest
        # is not Moss's: CI builds its demo-scope safety catalog from it
        # (.github/workflows/ci.yml, scripts/ingest_safety_documents.py).
        self.assertFalse((ROOT / "scripts" / "setup_moss_demo.py").exists())
        self.assertTrue((ROOT / "data" / "moss_demo" / "approved_documents.ko.json").is_file())


class UnusedSearchTests(unittest.TestCase):
    def test_the_voice_search_nothing_calls_is_gone_and_the_audit_retrieval_stays(self):
        # Nothing in src called search_safety_documents once lane CL deleted the
        # tools that did; scripts/audit_approved_catalog.py reads the catalog
        # through retrieve_approved_lab_documents.
        self.assertFalse(hasattr(retrieval, "search_safety_documents"))
        self.assertTrue(callable(retrieval.retrieve_approved_lab_documents))

    def test_no_source_file_still_names_it(self):
        found = []
        for folder in ("src", "scripts"):
            for path in sorted((ROOT / folder).rglob("*")):
                if path.is_file() and path.suffix in {".py", ".sh", ".html"} and "egg-info" not in str(path):
                    if "search_safety_documents" in path.read_text(encoding="utf-8", errors="ignore"):
                        found.append(str(path.relative_to(ROOT)))
        self.assertEqual(found, [])


class PilotBannerTests(_LauncherCopy):
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

    def test_the_banner_lists_no_moss_feature(self):
        result = self._run("--check-only")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIsNone(MOSS.search(result.stdout), result.stdout)


if __name__ == "__main__":
    unittest.main()
