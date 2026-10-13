"""Lane PL, stage 2c: the pilot data deletion tool (scripts/pilot_data_delete.py).

The pilot notice promises that collected data is used only to improve the
service and deleted when the pilot ends. A deletion cannot be undone, so the
tool goes preview -> confirm -> delete -> record, works only on the pilot data
folder, and keeps only what was deleted and when.

Every data store here is made by the product's own code in a temporary folder
(the pilot folder of a temporary copy of the repository layout); the repository's
own data/runtime is never read or written. Nothing here calls a provider.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from tests.test_launcher_settings_win import SERVE_MARKER, _LauncherCopy
from tests.test_protocol_catalog import write_text_pdf
from voiney_lab.experiment_protocol_config import ProtocolPersistenceSettings
from voiney_lab.experiment_protocol_store import initialize_protocol_store
from voiney_lab.experiment_reports import ExperimentReportStore
from voiney_lab.protocol_catalog import ProtocolCatalog
from voiney_lab.workspace_store import WorkspaceSettings, initialize_workspace_store

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "pilot_data_delete.py"
SECRET_TEXT = "Fictional participant protocol text 7731."


def load_tool(root: Path):
    """The tool as copied into ``root/scripts``, so its folders are root's."""

    (root / "scripts").mkdir(exist_ok=True)
    copy = root / "scripts" / "pilot_data_delete.py"
    shutil.copy2(SCRIPT, copy)
    spec = importlib.util.spec_from_file_location(f"pilot_data_delete_{abs(hash(str(root)))}", copy)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _PilotFolder(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.addCleanup(self._temp.cleanup)
        self.tool = load_tool(self.root)
        self.pilot = self.root / "data" / "runtime" / "pilot"
        self.log = self.pilot / "deletion_log.jsonl"

    def fill(self) -> dict[str, int]:
        """A pilot folder with one of each kind of record, made by the product's own stores."""

        self.pilot.mkdir(parents=True, exist_ok=True)
        # Uploaded original and its catalog record.
        settings = ProtocolPersistenceSettings(True, self.pilot)
        store = initialize_protocol_store(settings)
        pdf = self.root / "upload.pdf"
        write_text_pdf(pdf, SECRET_TEXT, title="Fictional")
        ProtocolCatalog(store).register(pdf, source_filename="upload.pdf", media_type="application/pdf")
        store.close()
        # An experiment record.
        reports = ExperimentReportStore(self.pilot / "experiment_reports.sqlite")
        reports.open_report(
            session_id="session-1", protocol_id="protocol-1", protocol_title="Fictional",
            protocol_revision="pdf-1", protocol_sha256="0" * 64,
            readiness_status="guidance_ready", development_only=False)
        # The workspace (experiment sessions, translations, settings) and a
        # piece of evidence beside it.
        workspace = initialize_workspace_store(
            WorkspaceSettings(True, self.pilot / "workspace", 90))
        workspace.close()
        evidence = self.pilot / "workspace" / "evidence" / "bucket" / "session"
        evidence.mkdir(parents=True)
        (evidence / "photo.jpg").write_bytes(b"fictional photo bytes")
        # Voice diagnostics.
        diagnostics = self.pilot / "stt-diagnostics"
        diagnostics.mkdir()
        (diagnostics / "turn-1.wav").write_bytes(b"RIFF fictional audio")
        (diagnostics / "turn-1.json").write_text(
            json.dumps({"raw_transcript": SECRET_TEXT}), encoding="utf-8")
        # What is not participant data.
        (self.pilot / "approved_safety_catalog.sqlite").write_bytes(b"institution catalog stand-in")
        return {"pdf": 1, "reports": 1}

    def run_tool(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = self.tool.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def snapshot(self, folder: Path | None = None) -> dict[str, int]:
        base = folder or self.pilot
        return {
            path.relative_to(base).as_posix(): path.stat().st_size
            for path in sorted(base.rglob("*")) if path.is_file()
        }

    def word(self, preview: str) -> str:
        for line in preview.splitlines():
            if "--confirm" in line:
                return line.split("--confirm", 1)[1].strip()
        self.fail("the preview named no confirmation word:\n" + preview)


class PreviewTests(_PilotFolder):
    def test_the_preview_says_what_and_how_many_and_deletes_nothing(self):
        self.fill()
        before = self.snapshot()
        code, out, err = self.run_tool()
        self.assertEqual((code, err), (0, ""), out)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.log.exists())
        self.assertIn("아무것도 지우지 않았습니다", out)
        for label in (
            "업로드한 프로토콜 원문 PDF", "분석·OCR·확인 질문 기록", "실험 기록",
            "실험 세션·관찰·번역 문장·증거 파일·설정", "음성 진단 녹음과 전사",
        ):
            self.assertIn(label, out)
        self.assertIn("업로드 PDF 1", out)
        self.assertIn("실험 기록 1", out)
        self.assertIn("증거 파일 1", out)
        self.assertIn("PDF 1", out)  # the original, counted in its folder
        self.assertIn("녹음(wav) 1", out)
        self.assertIn("전사 기록(json) 1", out)
        self.assertIn("파일 2개", out)  # the two voice-diagnostic files
        self.assertIn("approved_safety_catalog.sqlite", out)
        self.assertIn("지우지 않는 것", out)
        self.assertRegex(out, r"--delete --confirm [0-9A-F]{6}\b")

    def test_the_preview_never_prints_what_a_participant_said_or_uploaded(self):
        self.fill()
        _, out, _ = self.run_tool()
        self.assertNotIn(SECRET_TEXT, out)
        self.assertNotIn("upload.pdf", out)

    def test_an_empty_or_missing_pilot_folder_has_nothing_to_delete(self):
        code, out, _ = self.run_tool()  # the folder is not there yet
        self.assertEqual(code, 0)
        self.assertIn("지울 데이터가 없습니다", out)
        self.pilot.mkdir(parents=True)
        code, out, _ = self.run_tool()
        self.assertEqual(code, 0)
        self.assertIn("지울 데이터가 없습니다", out)
        code, out, _ = self.run_tool("--delete", "--confirm", "ABCDEF")
        self.assertEqual(code, 0)
        self.assertFalse(self.log.exists())


class DeleteTests(_PilotFolder):
    def test_it_deletes_what_the_preview_listed_after_the_word_and_records_it(self):
        self.fill()
        _, preview, _ = self.run_tool()
        word = self.word(preview)
        code, out, err = self.run_tool("--delete", "--confirm", word)
        self.assertEqual((code, err), (0, ""), out)
        self.assertEqual(
            sorted(self.snapshot()),
            ["approved_safety_catalog.sqlite", "deletion_log.jsonl"],
        )
        self.assertEqual(
            (self.pilot / "approved_safety_catalog.sqlite").read_bytes(),
            b"institution catalog stand-in")
        # A second preview finds nothing: the stores are gone, not emptied.
        _, again, _ = self.run_tool()
        self.assertIn("지울 데이터가 없습니다", again)

    def test_the_record_holds_what_and_when_and_nothing_a_person_said(self):
        self.fill()
        _, preview, _ = self.run_tool()
        word = self.word(preview)
        self.run_tool("--delete", "--confirm", word)
        lines = self.log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        record = json.loads(lines[0])
        self.assertRegex(record["deleted_at"], r"^20\d\d-\d\d-\d\dT\d\d:\d\d:\d\d")
        self.assertEqual(record["result"], "deleted")
        self.assertEqual(record["confirmation"], word)
        categories = {item["category"]: item for item in record["categories"]}
        self.assertEqual(
            set(categories),
            {"uploaded_originals", "analysis_records", "experiment_records",
             "workspace_records", "voice_records"})
        self.assertTrue(all(item["result"] == "deleted" for item in categories.values()))
        self.assertEqual(categories["voice_records"]["files"], 2)
        self.assertEqual(categories["uploaded_originals"]["files"], 1)
        self.assertEqual(categories["experiment_records"]["rows"]["experiment_reports"], 1)
        text = self.log.read_text(encoding="utf-8")
        for forbidden in (SECRET_TEXT, "upload.pdf", "photo.jpg", "turn-1", "bucket", "session-1"):
            self.assertNotIn(forbidden, text)
        self.assertNotIn(os.environ.get("USER", "\0"), text)

    def test_a_second_deletion_adds_a_second_record(self):
        for _ in range(2):
            self.fill()
            _, preview, _ = self.run_tool()
            self.run_tool("--delete", "--confirm", self.word(preview))
        self.assertEqual(len(self.log.read_text(encoding="utf-8").splitlines()), 2)

    def test_without_the_word_or_with_a_wrong_one_nothing_is_deleted(self):
        self.fill()
        before = self.snapshot()
        for arguments in (("--delete",), ("--delete", "--confirm", "000000"), ("--confirm", "ABCDEF")):
            with self.subTest(arguments=arguments):
                code, out, err = self.run_tool(*arguments)
                if "--delete" in arguments:
                    self.assertEqual(code, 2, out + err)
                    self.assertIn("[REFUSED]", err)
                else:
                    self.assertEqual(code, 0)  # a word without --delete is only a preview
                self.assertEqual(self.snapshot(), before)
                self.assertFalse(self.log.exists())

    def test_a_word_from_a_preview_no_longer_fits_when_the_folder_changed(self):
        self.fill()
        _, preview, _ = self.run_tool()
        word = self.word(preview)
        (self.pilot / "stt-diagnostics" / "turn-2.wav").write_bytes(b"one more recording")
        before = self.snapshot()
        code, out, err = self.run_tool("--delete", "--confirm", word)
        self.assertEqual(code, 2, out)
        self.assertIn("확인 단어가 맞지 않습니다", err)
        self.assertEqual(self.snapshot(), before)

    def test_it_refuses_while_another_process_holds_a_file_open(self):
        if not Path("/proc/self/fd").is_dir():
            self.skipTest("needs /proc")
        self.fill()
        _, preview, _ = self.run_tool()
        word = self.word(preview)
        holder = subprocess.Popen([
            sys.executable, "-c",
            "import sys,time; f=open(sys.argv[1],'rb'); print('open',flush=True); time.sleep(60)",
            str(self.pilot / "experiment_reports.sqlite"),
        ], stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(holder.stdout.readline().strip(), "open")
            before = self.snapshot()
            code, out, err = self.run_tool("--delete", "--confirm", word)
            self.assertEqual(code, 2, out)
            self.assertIn(f"pid {holder.pid}", err)
            self.assertEqual(self.snapshot(), before)
        finally:
            holder.kill()
            holder.wait()
        code, out, err = self.run_tool("--delete", "--confirm", word)
        self.assertEqual((code, err), (0, ""), out)


class RefusalTests(_PilotFolder):
    def test_a_development_data_folder_is_refused_even_with_the_pilot_mark(self):
        for name in ("candidate-a-live-acceptance", "ci-e2e", "stt-diagnostics", "candidate-a-source"):
            with self.subTest(folder=name):
                folder = self.root / "data" / "runtime" / name
                folder.mkdir(parents=True)
                (folder / "experiment_reports.sqlite").write_bytes(b"development data")
                (folder / ".voiney-pilot-data").write_text("", encoding="utf-8")
                for arguments in (("--data-dir", str(folder)), ("--data-dir", str(folder), "--delete", "--confirm", "AAAAAA")):
                    code, out, err = self.run_tool(*arguments)
                    self.assertEqual(code, 2, out)
                    self.assertIn("개발 데이터 폴더에서는 쓸 수 없습니다", err)
                self.assertTrue((folder / "experiment_reports.sqlite").is_file())

    def test_the_development_cache_and_the_fixtures_are_refused(self):
        for relative in ("data/development_cache", "data/development_cache/chunk_analysis", "data/fixtures"):
            with self.subTest(folder=relative):
                folder = self.root / relative
                folder.mkdir(parents=True)
                (folder / ".voiney-pilot-data").write_text("", encoding="utf-8")
                code, out, err = self.run_tool("--data-dir", str(folder))
                self.assertEqual(code, 2, out)

    def test_a_folder_that_is_not_the_pilot_folder_and_has_no_mark_is_refused(self):
        for folder in (self.root, self.root / "data", self.root / "data" / "runtime", Path("/tmp")):
            with self.subTest(folder=str(folder)):
                folder.mkdir(parents=True, exist_ok=True)
                code, out, err = self.run_tool("--data-dir", str(folder), "--delete", "--confirm", "AAAAAA")
                self.assertEqual(code, 2, out)
                self.assertIn("파일럿 데이터 폴더가 아닙니다", err)

    def test_a_folder_with_the_pilot_mark_is_accepted(self):
        folder = self.root / "elsewhere" / "pilot-copy"
        (folder / "objects" / "sha256" / "ab").mkdir(parents=True)
        (folder / "objects" / "sha256" / "ab" / "x.pdf").write_bytes(b"%PDF fictional")
        (folder / ".voiney-pilot-data").write_text("", encoding="utf-8")
        _, preview, _ = self.run_tool("--data-dir", str(folder))
        code, out, err = self.run_tool("--data-dir", str(folder), "--delete", "--confirm", self.word(preview))
        self.assertEqual((code, err), (0, ""), out)
        self.assertFalse((folder / "objects").exists())
        self.assertTrue((folder / "deletion_log.jsonl").is_file())

    def test_a_symbolic_link_inside_the_folder_is_refused(self):
        self.fill()
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "precious.txt").write_text("not pilot data", encoding="utf-8")
        os.symlink(outside, self.pilot / "stt-diagnostics" / "link")
        before = self.snapshot(outside)
        code, out, err = self.run_tool()
        self.assertEqual(code, 2, out)
        self.assertIn("심볼릭 링크", err)
        code, out, err = self.run_tool("--delete", "--confirm", "AAAAAA")
        self.assertEqual(code, 2)
        self.assertEqual(self.snapshot(outside), before)

    def test_a_pilot_folder_that_is_itself_a_link_is_refused(self):
        real = self.root / "real-pilot"
        real.mkdir()
        self.pilot.parent.mkdir(parents=True)
        os.symlink(real, self.pilot)
        code, out, err = self.run_tool()
        self.assertEqual(code, 2, out)

    def test_files_the_tool_does_not_know_are_listed_and_left_alone(self):
        self.fill()
        (self.pilot / "notes-from-the-pilot-owner.txt").write_text("keep me", encoding="utf-8")
        _, preview, _ = self.run_tool()
        self.assertIn("notes-from-the-pilot-owner.txt", preview)
        self.run_tool("--delete", "--confirm", self.word(preview))
        self.assertEqual((self.pilot / "notes-from-the-pilot-owner.txt").read_text(encoding="utf-8"), "keep me")


class PilotLauncherLeavesTheMarkTests(_LauncherCopy):
    """scripts/run_pilot.sh marks its folder and keeps voice diagnostics inside it."""

    SCRIPT = "run_pilot.sh"

    def stand_in_python(self, dump: Path) -> None:
        python = self.root / ".venv" / "bin" / "python"
        python.write_text(
            "#!/bin/sh\n"
            f'case " $* " in *" uvicorn "*) env | grep "^VOINEY_LAB_STT_DIAGNOSTIC_DIR=" > "{dump}"; '
            f'echo "{SERVE_MARKER}" >&2; exit 97;; esac\n'
            f'exec "{sys.executable}" "$@"\n',
            encoding="utf-8",
        )

    def test_a_real_start_marks_the_folder_and_pins_the_voice_diagnostics_inside_it(self):
        dump = self.root / "env.txt"
        self.stand_in_python(dump)
        pilot = self.root / "data" / "runtime" / "pilot"
        result = self._run(VOINEY_LAB_STT_DIAGNOSTIC_DIR=str(self.root / "data" / "runtime" / "stt-diagnostics"))
        self.assertEqual(result.returncode, 97, result.stdout + result.stderr)
        self.assertTrue((pilot / ".voiney-pilot-data").is_file())
        self.assertEqual(
            dump.read_text(encoding="utf-8").strip(),
            f"VOINEY_LAB_STT_DIAGNOSTIC_DIR={pilot}/stt-diagnostics")
        self.assertIn("[WARN] VOINEY_LAB_STT_DIAGNOSTIC_DIR was set in the environment.", result.stdout)
        self.assertIn(f"STT_DIAG   = {pilot}/stt-diagnostics", result.stdout)

    def test_check_only_marks_nothing(self):
        result = self._run("--check-only")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.root / "data").exists())


if __name__ == "__main__":
    unittest.main()
