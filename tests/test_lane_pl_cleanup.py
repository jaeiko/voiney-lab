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
