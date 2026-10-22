"""Lane ON, decision ① (2026-10-18): ``scripts/pilot_invite.py``.

The only way into an ``accounts`` pilot is an invite link the server's
shell made with this tool: shown once, kept as a hash, expiring, one use.
The tool also lists invites and accounts, revokes an invite and switches
an account off or on. Everything runs on a temporary folder; nothing here
reads the repository's own data or reaches the network.
"""

from __future__ import annotations

import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from voiney_lab import accounts as acc

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "pilot_invite.py"
LINK = re.compile(r"^\s+(https?://\S+)/\?invite=(\S+)$", re.MULTILINE)


def run_tool(*arguments: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    shell = {key: value for key, value in os.environ.items() if key != "VOINEY_LAB_WORKSPACE_DATA_DIR"}
    shell.update(env or {})
    return subprocess.run(
        [sys.executable, str(SCRIPT), *arguments], cwd=ROOT, env=shell,
        capture_output=True, text=True, timeout=120,
    )


class InviteToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.folder = Path(self._temp.name) / "workspace"
        self.db = self.folder / acc.ACCOUNTS_DATABASE_FILENAME

    def test_a_link_is_printed_once_and_only_its_hash_is_kept(self):
        result = run_tool("--data-dir", str(self.folder), "--base-url", "https://pilot.example.org/",
                          "--admin", "--hours", "24", "--note", "첫 관리자")
        self.assertEqual(result.returncode, 0, result.stderr)
        match = LINK.search(result.stdout)
        self.assertIsNotNone(match, result.stdout)
        base, token = match.groups()
        self.assertEqual(base, "https://pilot.example.org")
        self.assertIn("24시간 뒤 만료", result.stdout)
        self.assertIn("관리자 예", result.stdout)
        self.assertIn("이용 권한 pilot", result.stdout)
        self.assertTrue(self.db.is_file())
        raw = "".join(str(value) for row in sqlite3.connect(self.db).execute("SELECT * FROM invites") for value in row)
        self.assertNotIn(token, raw, "the token is not kept")
        store = acc.AccountStore.open(self.db)
        try:
            invite = store.invite_for_token(token)
            self.assertTrue(invite.admin)
            self.assertEqual(invite.note, "첫 관리자")
            account = store.sign_up(token, "김재준", "correct horse battery")
            self.assertTrue(account.admin)
        finally:
            store.close()
        # Used once: the same link opens no second account.
        again = acc.AccountStore.open(self.db)
        try:
            with self.assertRaises(acc.InviteInvalidError):
                again.sign_up(token, "다른 사람", "correct horse battery")
        finally:
            again.close()

    def test_the_default_link_and_lab_and_the_shells_folder(self):
        # The folder is now explicit; never let this subprocess write runtime.
        result = run_tool("--data-dir", str(self.folder), env={"VOINEY_LAB_WORKSPACE_DATA_DIR": str(self.folder)})
        self.assertEqual(result.returncode, 0, result.stderr)
        base, _ = LINK.search(result.stdout).groups()
        self.assertEqual(base, "http://127.0.0.1:8080")
        self.assertIn("72시간 뒤 만료", result.stdout)
        self.assertIn("연구실 lab-pilot", result.stdout)
        self.assertIn("관리자 아니오", result.stdout)
        self.assertTrue(self.db.is_file(), "the explicitly selected folder was used")

    def test_listing_revoking_and_switching_accounts_off_and_on(self):
        first = run_tool("--data-dir", str(self.folder), "--note", "최수진")
        token = LINK.search(first.stdout).group(2)
        second = run_tool("--data-dir", str(self.folder), "--lab", "lab-b")
        invite_id = re.search(r"초대 id (invite-[0-9a-f]+)", second.stdout).group(1)
        listed = run_tool("--data-dir", str(self.folder), "--list")
        self.assertEqual(listed.returncode, 0, listed.stderr)
        lines = [line for line in listed.stdout.splitlines() if line.startswith("invite-")]
        self.assertEqual(len(lines), 2)
        self.assertIn("열림", lines[0])
        self.assertIn("최수진", lines[0])
        self.assertIn("연구실 lab-b", lines[1])
        self.assertNotIn(token, listed.stdout)

        revoked = run_tool("--data-dir", str(self.folder), "--revoke", invite_id)
        self.assertEqual(revoked.returncode, 0, revoked.stderr)
        self.assertIn("취소", revoked.stdout)
        listed = run_tool("--data-dir", str(self.folder), "--list")
        self.assertIn("취소됨", listed.stdout)
        unknown = run_tool("--data-dir", str(self.folder), "--revoke", "invite-nope")
        self.assertEqual(unknown.returncode, 2)

        store = acc.AccountStore.open(self.db)
        try:
            account = store.sign_up(token, "최수진", "correct horse battery")
            session = store.open_session(account)
        finally:
            store.close()
        listed = run_tool("--data-dir", str(self.folder), "--list")
        self.assertIn("사용됨", listed.stdout)
        accounts = run_tool("--data-dir", str(self.folder), "--accounts")
        self.assertEqual(accounts.returncode, 0, accounts.stderr)
        self.assertIn("최수진", accounts.stdout)
        self.assertIn("켜짐", accounts.stdout)

        off = run_tool("--data-dir", str(self.folder), "--disable", " 최수진 ")
        self.assertEqual(off.returncode, 0, off.stderr)
        self.assertIn("껐습니다", off.stdout)
        store = acc.AccountStore.open(self.db)
        try:
            self.assertIsNone(store.account_for_session(session), "the open session ended")
            with self.assertRaises(acc.AccountDisabledError):
                store.log_in("최수진", "correct horse battery")
        finally:
            store.close()
        self.assertIn("꺼짐", run_tool("--data-dir", str(self.folder), "--accounts").stdout)
        on = run_tool("--data-dir", str(self.folder), "--enable", "최수진")
        self.assertEqual(on.returncode, 0, on.stderr)
        store = acc.AccountStore.open(self.db)
        try:
            self.assertEqual(store.log_in("최수진", "correct horse battery").name, "최수진")
        finally:
            store.close()
        nobody = run_tool("--data-dir", str(self.folder), "--disable", "없는 사람")
        self.assertEqual(nobody.returncode, 2)
        self.assertIn("그런 계정이 없습니다", nobody.stderr)

    def test_listing_an_empty_folder_makes_nothing(self):
        result = run_tool("--data-dir", str(self.folder), "--list")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("아직 없습니다", result.stdout)
        self.assertFalse(self.db.exists())

    def test_a_bad_lifetime_or_lab_is_refused(self):
        self.assertEqual(run_tool("--data-dir", str(self.folder), "--hours", "0").returncode, 2)
        self.assertEqual(run_tool("--data-dir", str(self.folder), "--lab", "bad lab").returncode, 2)


if __name__ == "__main__":
    unittest.main()
