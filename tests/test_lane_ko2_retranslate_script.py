"""Lane KO2, decision 1: ``scripts/retranslate_protocols.py`` makes stored Korean again.

The person runs it with the translation role's key in the shell; here every
call is fake (contract-tested). Without ``--run`` it prints, per protocol, the
sentences the current style version lacks and the calls a run would make,
and calls nothing. With ``--run`` it adds the current version's rows beside
the old ones through the server's own generation, within ``--max-calls``; a
second plan finds nothing left. The repository's ``data/runtime/`` and a run
without a key are refused before anything is sent.
"""

from __future__ import annotations

import importlib.util
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import AsyncMock, patch

from tests.test_protocol_catalog import analysis_draft, write_text_pdf
from tests.test_revision_translations import FakeTranslator
from voiney_lab.experiment_protocol_config import ProtocolPersistenceSettings
from voiney_lab.experiment_protocol_store import initialize_protocol_store
from voiney_lab.protocol_catalog import ProtocolCatalog
from voiney_lab.protocol_translation import (
    STYLE_VERSION,
    GlossaryEntry,
    GlossaryResult,
    source_sha256,
    translation_revision_key,
)
from voiney_lab.workspace_store import (
    FactTranslationRecord,
    WorkspaceSettings,
    initialize_workspace_store,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "retranslate_protocols.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("retranslate_protocols", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _Counting:
    def __init__(self) -> None:
        self.translator = FakeTranslator()
        self.glossary_calls = 0

    async def glossary(self, context):
        self.glossary_calls += 1
        return GlossaryResult(entries=(GlossaryEntry("Wear", "Wear", True),), model_version="g")

    def factory(self, role):
        return self.translator, self.glossary


class RetranslateScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.script = _load_script()
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)
        self.data = self.root / "catalog"
        store = initialize_protocol_store(ProtocolPersistenceSettings(True, self.data))
        try:
            catalog = ProtocolCatalog(store)
            pdf = self.root / "sample.pdf"
            write_text_pdf(
                pdf, "Protocol Test\nSection preparation\n1. Add solution.\nWear gloves.",
                title="Protocol Test")
            registration = catalog.register(pdf, source_filename="sample.pdf", media_type="application/pdf")
            self.protocol_id = registration.entry.protocol_id
            draft = analysis_draft(pdf, self.protocol_id, "Protocol Test")
            store.append_analysis_revision(
                self.protocol_id, 1, f"analysis-{registration.entry.source_sha256[:24]}",
                draft.protocol, draft.readiness, draft.capability_policy_id)
            self.fixture = catalog.load_executable_fixture(self.protocol_id)
        finally:
            store.close()
        workspace = initialize_workspace_store(WorkspaceSettings(True, self.data / "workspace"))
        try:
            step = self.fixture.steps[0]
            # An old row (no version) that today's check refuses: "1" became "2".
            workspace.record_fact_translations([FactTranslationRecord(
                translation_revision_key(self.fixture), f"{step.step_id}/current_step", "ko",
                source_sha256(step.instruction_source_text), "1단계: 용액 2개를 넣습니다.",
                "machine", "quantities_changed", "old", "old", "2026-10-01T00:00:00+00:00")])
        finally:
            workspace.close()

    def main(self, *argv, factory=None):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            status = self.script.main(list(argv), translator_factory=factory)
        return status, out.getvalue(), err.getvalue()

    def rows(self):
        workspace = initialize_workspace_store(WorkspaceSettings(True, self.data / "workspace"))
        try:
            return workspace.fact_translations(translation_revision_key(self.fixture), "ko")
        finally:
            workspace.close()

    def test_the_plan_counts_what_the_current_version_lacks_and_calls_nothing(self) -> None:
        fake = _Counting()
        status, out, _ = self.main("--data-dir", str(self.data), factory=fake.factory)
        self.assertEqual(status, 0)
        self.assertEqual((fake.translator.batches, fake.glossary_calls), ([], 0))
        self.assertIn(f"번역 지침 판 v{STYLE_VERSION}", out)
        self.assertIn("Protocol Test", out)
        line = next(item for item in out.splitlines() if item.startswith(f"| Protocol Test · {self.protocol_id}"))
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        # sentences, Korean source, reviewed, already current, to make, calls
        self.assertEqual(cells[4], "0")
        self.assertGreaterEqual(int(cells[5]), 1)
        self.assertEqual(cells[6], f"{1 + 1} (1+1)")
        self.assertEqual(len(self.rows()), 1)

    def test_a_run_adds_the_current_version_beside_the_old_rows(self) -> None:
        fake = _Counting()
        report = self.root / "report"
        status, out, _ = self.main(
            "--data-dir", str(self.data), "--run", "--report-dir", str(report), "--compare", "2",
            factory=fake.factory)
        self.assertEqual(status, 0)
        self.assertEqual(fake.glossary_calls, 1)
        self.assertEqual(len(fake.translator.batches), 1)
        rows = self.rows()
        self.assertEqual(rows[0].style_version, 0)
        self.assertEqual({row.style_version for row in rows[1:]}, {STYLE_VERSION})
        self.assertIn("보낸 호출", out)
        self.assertTrue((report / "retranslate_result.md").is_file())
        comparison = (report / "comparison.md").read_text()
        self.assertIn("새 번역", comparison)
        # Nothing is left for a second run.
        again = _Counting()
        status, out, _ = self.main("--data-dir", str(self.data), "--run", factory=again.factory)
        self.assertEqual((status, again.translator.batches, again.glossary_calls), (0, [], 0))

    def test_max_calls_stops_sending(self) -> None:
        fake = _Counting()
        status, _, _ = self.main("--data-dir", str(self.data), "--run", "--max-calls", "1",
                                 factory=fake.factory)
        self.assertEqual(status, 0)
        self.assertEqual(fake.glossary_calls + len(fake.translator.batches), 1)
        self.assertEqual(len(self.rows()), 1)  # the glossary call only: no sentence stored

    def test_the_repository_runtime_folder_and_a_missing_key_are_refused(self) -> None:
        status, _, err = self.main("--data-dir", str(ROOT / "data" / "runtime" / "x"))
        self.assertEqual(status, 2)
        self.assertIn("--allow-runtime-dir", err)
        with patch.dict(os.environ, {"VOINEY_LAB_TRANSLATION_PROVIDER": ""}):
            status, _, err = self.main("--data-dir", str(self.data), "--run")
        self.assertEqual(status, 2)
        self.assertIn(".env", err)
        self.assertEqual(len(self.rows()), 1)

    def test_main_splits_a_timeout_and_keeps_the_global_call_cap(self) -> None:
        fake = _Counting()
        batches = []

        async def translate(batch, context):
            batches.append(len(batch))
            if len(batch) > 1:
                raise TimeoutError()
            return await fake.translator(batch, context)

        with patch("asyncio.sleep", new=AsyncMock()):
            status, out, _ = self.main(
                "--data-dir", str(self.data), "--run", "--max-calls", "5",
                factory=lambda role: (translate, fake.glossary))
        self.assertEqual(status, 0)
        # Glossary + three attempts of the original batch + one successful half.
        self.assertEqual(fake.glossary_calls + len(batches), 5)
        self.assertGreater(batches[0], 1)
        self.assertEqual(batches[:3], [batches[0]] * 3)
        self.assertEqual(batches[-1], 1)
        self.assertEqual(len(self.rows()), 2)  # old row plus the successful half
        self.assertIn("180초", out)

    def test_the_script_never_imports_the_server(self) -> None:
        # Importing server.py would read the repository .env (keys).
        self.assertNotIn("voiney_lab.server", SCRIPT.read_text())
        self.assertNotIn("dotenv", SCRIPT.read_text())


if __name__ == "__main__":
    unittest.main()
