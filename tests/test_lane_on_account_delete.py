"""Lane ON, decision ① (2026-10-18): the pilot deletion tool deletes one account's data.

``scripts/pilot_data_delete.py --account NAME`` previews and, with the
confirmation word, deletes what that account left -- its experiment
sessions with their events, completed steps, observations and evidence
(rows and files), the experiment records bound to it, its settings, access
records and resource ownership, and the account itself with its sessions and
login attempts -- and nothing of anyone else. The append-only triggers are
lifted for the deletion and put back. The lab's shared protocol list stays.
Every store here is made by the product's own code in a temporary folder.
"""

from __future__ import annotations

import io
import json
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from tests.test_lane_pl_pilot_data_delete import load_tool
from voiney_lab import accounts as acc
from voiney_lab.experiment_reports import ExperimentReportStore
from voiney_lab.workspace_store import WorkspaceSettings, initialize_workspace_store

PASSWORD = "correct horse battery"


class AccountDeleteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.addCleanup(self._temp.cleanup)
        self.tool = load_tool(self.root)
        self.pilot = self.root / "data" / "runtime" / "pilot"
        self.pilot.mkdir(parents=True)
        (self.pilot / self.tool.PILOT_MARKER).write_text("pilot\n")
        self.workspace = self.pilot / "workspace"
        self.accounts = acc.AccountStore.open(self.workspace / acc.ACCOUNTS_DATABASE_FILENAME)
        self.addCleanup(self.accounts.close)
        self.store = initialize_workspace_store(WorkspaceSettings(enabled=True, data_dir=self.workspace))
        self.addCleanup(self.store.close)
        self.reports = ExperimentReportStore(self.pilot / "experiment_reports.sqlite")
        self.a = self._person("김재준", admin=True)
        self.b = self._person("최수진")
        self._experiment(self.a, "exp-a", with_capture=True)
        self._experiment(self.b, "exp-b", with_capture=False)
        # The lab's shared protocol list: A uploaded one.
        self.store.bind_resource(self.a.principal(), "protocol_catalog", "protocol-shared")
        self.workspace_db = self.workspace / "commercial_workspace.sqlite"

    def _person(self, name: str, *, admin: bool = False) -> acc.Account:
        token, _ = self.accounts.create_invite(admin=admin)
        account = self.accounts.sign_up(token, name, PASSWORD)
        self.accounts.open_session(account)
        try:
            self.accounts.log_in(name, "wrong wrong wrong")
        except acc.LoginRefusedError:
            pass
        principal = account.principal()
        self.store.bootstrap_principal(principal)
        self.store.record_workspace_access(principal)
        self.store.record_experimenter_setting(principal, name="confirm_mode", value="quiet", source="screen")
        return account

    def _experiment(self, account: acc.Account, session_id: str, *, with_capture: bool) -> None:
        principal = account.principal()
        self.store.start_experiment(
            principal, protocol_id="protocol-shared", protocol_revision_id="pdf-1",
            session_id=session_id, current_step_id="step-1", current_step_label="1")
        self.store.transition_experiment(
            principal, session_id, action="resume", expected_version=1, event_key=f"{session_id}-go")
        report = self.reports.open_report(
            session_id=session_id, protocol_id="protocol-shared", protocol_title="Shared",
            protocol_revision="pdf-1", protocol_sha256="a" * 64, readiness_status="guidance_ready",
            development_only=False)
        self.reports.append_event(report["report_id"], event_key="experimenter", event_type="experimenter_recorded",
                                  payload={"display_name": account.name})
        self.store.bind_resource(principal, "experiment_report", report["report_id"])
        if with_capture:
            self.store.record_observation(
                principal, session_id, event_key=f"{session_id}-obs", content="sample looks cloudy",
                category="appearance", capture_source="manual")
            file = self.workspace / "evidence" / "lab" / session_id / "photo.png"
            file.parent.mkdir(parents=True)
            file.write_bytes(b"\x89PNG not really")
            self.store.record_evidence(
                principal, session_id, event_key=f"{session_id}-ev", evidence_kind="image",
                original_filename="photo.png", media_type="image/png", byte_size=file.stat().st_size,
                sha256="b" * 64, storage_reference=f"evidence/lab/{session_id}/photo.png")

    def run_tool(self, *arguments: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = self.tool.main(["--data-dir", str(self.pilot), *arguments])
        return code, out.getvalue(), err.getvalue()

    def _counts(self, path: Path, table: str, where: str = "", parameters=()) -> int:
        connection = sqlite3.connect(path)
        try:
            return connection.execute(f"SELECT COUNT(*) FROM {table} {where}", parameters).fetchone()[0]
        finally:
            connection.close()

    def _triggers(self) -> int:
        return self._counts(self.workspace_db, "sqlite_master", "WHERE type='trigger'")

    def test_the_preview_names_the_accounts_data_and_deletes_nothing(self):
        before = {path: path.stat().st_mtime_ns for path in self.pilot.rglob("*") if path.is_file()}
        code, out, err = self.run_tool("--account", "김재준")
        self.assertEqual(code, 0, err)
        self.assertIn("계정: 김재준", out)
        self.assertIn("실험 세션 1", out)
        self.assertIn("관찰 기록 1", out)
        self.assertIn("증거 파일 기록 1", out)
        self.assertIn("파일 1개", out)
        self.assertIn("실험 기록 1", out)
        self.assertIn("실험자 설정 1", out)
        self.assertIn("계정 1", out)
        self.assertIn("로그인 세션 1", out)
        self.assertIn("로그인 시도 1", out)
        self.assertIn("프로토콜 1건은 연구실 공유라 남기고", out)
        self.assertIn("--account '김재준' --delete --confirm ", out)
        self.assertEqual({path: path.stat().st_mtime_ns for path in self.pilot.rglob("*") if path.is_file()}, before)
        self.assertFalse((self.pilot / self.tool.LOG_NAME).exists())

    def test_deleting_one_account_leaves_the_other_and_the_shared_protocol(self):
        triggers = self._triggers()
        _, preview, _ = self.run_tool("--account", "김재준")
        word = preview.split("--confirm ")[1].split()[0]
        code, out, err = self.run_tool("--account", "김재준", "--delete", "--confirm", word)
        self.assertEqual(code, 0, err + out)
        self.assertIn("deleted: workspace/commercial_workspace.sqlite", out)
        self.assertIn("deleted: experiment_reports.sqlite", out)
        self.assertIn("deleted: workspace/accounts.sqlite", out)
        a_id, b_id = self.a.principal().principal_id, self.b.principal().principal_id
        ws = self.workspace_db
        self.assertEqual(self._counts(ws, "experiment_sessions", "WHERE owner_principal_id=?", (a_id,)), 0)
        self.assertEqual(self._counts(ws, "experiment_sessions", "WHERE owner_principal_id=?", (b_id,)), 1)
        self.assertEqual(self._counts(ws, "experiment_session_events", "WHERE session_id='exp-a'"), 0)
        self.assertGreater(self._counts(ws, "experiment_session_events", "WHERE session_id='exp-b'"), 0)
        self.assertEqual(self._counts(ws, "experiment_observations"), 0)
        self.assertEqual(self._counts(ws, "experiment_evidence"), 0)
        self.assertEqual(self._counts(ws, "experimenter_settings", "WHERE principal_id=?", (a_id,)), 0)
        self.assertEqual(self._counts(ws, "experimenter_settings", "WHERE principal_id=?", (b_id,)), 1)
        self.assertEqual(self._counts(ws, "admin_audit_events", "WHERE actor_principal_id=?", (a_id,)), 0)
        self.assertEqual(self._counts(ws, "memberships", "WHERE principal_id=?", (a_id,)), 0)
        self.assertEqual(self._counts(ws, "resource_bindings", "WHERE owner_principal_id=? AND resource_type='protocol_catalog'", (a_id,)), 1)
        self.assertEqual(self._counts(ws, "resource_bindings", "WHERE owner_principal_id=? AND resource_type<>'protocol_catalog'", (a_id,)), 0)
        connection = sqlite3.connect(ws)
        name, subject = connection.execute("SELECT display_name,subject FROM principals WHERE principal_id=?", (a_id,)).fetchone()
        connection.close()
        self.assertEqual(name, "(삭제된 계정)")
        self.assertNotIn("김재준", subject)
        self.assertFalse((self.workspace / "evidence" / "lab" / "exp-a").exists())
        self.assertEqual(self._triggers(), triggers, "the append-only triggers are back")
        with self.assertRaises(sqlite3.IntegrityError):
            connection = sqlite3.connect(ws)
            connection.execute("DELETE FROM experiment_session_events WHERE session_id='exp-b'")
        rp = self.pilot / "experiment_reports.sqlite"
        self.assertEqual(self._counts(rp, "experiment_reports"), 1)
        self.assertEqual(self._counts(rp, "experiment_reports", "WHERE session_id='exp-b'"), 1)
        self.assertEqual(self._counts(rp, "experiment_report_events", "WHERE payload LIKE '%김재준%'"), 0)
        store = acc.AccountStore.open(self.workspace / acc.ACCOUNTS_DATABASE_FILENAME)
        try:
            self.assertEqual([item.name for item in store.list_accounts()], ["최수진"])
            self.assertEqual(store.log_in("최수진", PASSWORD).name, "최수진")
        finally:
            store.close()
        log = (self.pilot / self.tool.LOG_NAME).read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(log), 1)
        record = json.loads(log[0])
        self.assertEqual(record["scope"], "account")
        self.assertEqual(record["result"], "deleted")
        self.assertNotIn("김재준", log[0])
        self.assertNotIn(self.a.account_id, log[0])
        # Gone: the preview refuses the name now.
        code, _, err = self.run_tool("--account", "김재준")
        self.assertEqual(code, 2)
        self.assertIn("그런 계정이 없습니다", err)

    def test_a_person_without_shared_protocols_leaves_no_identity_row(self):
        _, preview, _ = self.run_tool("--account", "최수진")
        self.assertNotIn("연구실 공유", preview)
        word = preview.split("--confirm ")[1].split()[0]
        code, _, err = self.run_tool("--account", "최수진", "--delete", "--confirm", word)
        self.assertEqual(code, 0, err)
        b_id = self.b.principal().principal_id
        self.assertEqual(self._counts(self.workspace_db, "principals", "WHERE principal_id=?", (b_id,)), 0)
        self.assertEqual(self._counts(self.workspace_db, "experiment_sessions"), 1)

    def test_refusals(self):
        code, _, err = self.run_tool("--account", "없는 사람")
        self.assertEqual(code, 2)
        self.assertIn("그런 계정이 없습니다", err)
        code, _, err = self.run_tool("--account", "김재준", "--delete")
        self.assertEqual(code, 2)
        self.assertIn("--confirm", err)
        code, _, err = self.run_tool("--account", "김재준", "--delete", "--confirm", "000000")
        self.assertEqual(code, 2)
        self.assertIn("확인 단어", err)
        self.assertEqual(self._counts(self.workspace_db, "experiment_sessions"), 2)
        shutil.rmtree(self.workspace / "accounts.sqlite", ignore_errors=True)
        (self.workspace / "accounts.sqlite").unlink()
        code, _, err = self.run_tool("--account", "김재준")
        self.assertEqual(code, 2)
        self.assertIn("계정 저장소가 없습니다", err)


if __name__ == "__main__":
    unittest.main()
