"""Lane FX5, decision 5 (2026-10-20): the pilot guide's section 7 says what the code does now.

Section 7 used to say "to have accounts, write ``VOINEY_LAB_AUTH_MODE=accounts``
in ``.env``". The launcher has exported accounts by default since lane FX4
(the development identity only with ``--dev-identity``), and lane FX5 added
the account's own deletion, the admin screen's token-free opening and the
credential check. Every route, flag and file the section names must exist.
"""

from __future__ import annotations

import re
import subprocess
import sys
import unittest
from pathlib import Path

from voiney_lab import accounts as acc
from voiney_lab import server as server_module

ROOT = Path(__file__).resolve().parents[1]
GUIDE = (ROOT / "docs" / "PILOT_GUIDE.md").read_text(encoding="utf-8")


def section(number: str) -> str:
    start = GUIDE.index(f"## {number}. ")
    following = re.search(r"^## \d+\. ", GUIDE[start + 4:], flags=re.M)
    return GUIDE[start:start + 4 + following.start()] if following else GUIDE[start:]


class SectionSevenTests(unittest.TestCase):
    seven = section("7")

    def test_accounts_are_the_default_and_dev_is_the_flag(self) -> None:
        self.assertNotIn("`.env` 에 `VOINEY_LAB_AUTH_MODE=accounts` 를 적는다", self.seven)
        self.assertIn("계정이 기본이다", self.seven)
        self.assertIn("scripts/run_pilot.sh --dev-identity", self.seven)
        launcher = (ROOT / "scripts" / "run_pilot.sh").read_text(encoding="utf-8")
        self.assertIn('PILOT_AUTH_MODE="accounts"', launcher)
        self.assertIn("--dev-identity)", launcher)

    def test_the_deletion_and_credential_parts_are_written(self) -> None:
        for heading in ("### 7-6. 관리자 지표 화면", "### 7-7. 계정 지우기와 데이터 지우기", "### 7-8. 비밀 값은 어디에도 남지 않는다"):
            self.assertIn(heading, self.seven)
        for phrase in ("POST /api/auth/delete-account", "409 {\"detail\": \"last_admin\"", "account_delete_incomplete",
                       "deletion_log_unavailable", "--account", "파일럿 지표 행", "(삭제된 계정)",
                       "--revoke", "<token removed>"):
            self.assertIn(phrase, self.seven)

    def test_what_the_section_names_exists(self) -> None:
        paths = {route.path for route in server_module.app.routes}
        for path in ("/api/auth/delete-account", "/api/auth/accounts/{account_id}/disabled", "/api/admin/pilot-metrics"):
            self.assertIn(path, paths)
        invite_help = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "pilot_invite.py"), "--help"], capture_output=True, text=True, cwd=ROOT).stdout
        for flag in ("--admin", "--list", "--revoke", "--disable", "--enable", "--accounts"):
            self.assertIn(flag, invite_help)
        delete_help = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "pilot_data_delete.py"), "--help"], capture_output=True, text=True, cwd=ROOT).stdout
        for flag in ("--account", "--delete", "--confirm"):
            self.assertIn(flag, delete_help)
        self.assertEqual(acc.DELETION_LOG_NAME, "deletion_log.jsonl")
        self.assertIn("deletion_log.jsonl", self.seven)

    def test_the_last_admin_sentence_the_guide_quotes_is_the_servers(self) -> None:
        self.assertIn(server_module._LAST_ADMIN_MESSAGE_KO.split(" 계정")[0], self.seven)  # noqa: SLF001

    def test_the_caddy_example_keeps_its_access_log_off(self) -> None:
        caddy = (ROOT / "scripts" / "pilot_https" / "Caddyfile.example").read_text(encoding="utf-8")
        directives = [line.strip() for line in caddy.splitlines() if line.strip() and not line.strip().startswith("#")]
        self.assertNotIn("log", directives, "an access log would keep the invite token in the path")


if __name__ == "__main__":
    unittest.main()
