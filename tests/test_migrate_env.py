"""scripts/migrate_env.py on fake .env files (decision 4 of 2026-10-04).

check shows names only; write backs the file up (mode 600), renames the old
names, groups and sorts the settings with a short comment each, keeps every
value character for character, moves names the code does not read to the
bottom, and stops before writing anything when a name is duplicated. The old
names are taken from the rename map, so this file spells none of them.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from dotenv import dotenv_values

from voiney_lab.setting_names import AREAS, BY_NAME
from voiney_lab.setting_renames import OLD_PREFIX, RENAMED, renamed

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "migrate_env.py"
_spec = importlib.util.spec_from_file_location("migrate_env", SCRIPT)
migrate_env = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("migrate_env", migrate_env)  # its dataclasses look it up
_spec.loader.exec_module(migrate_env)

SECRET = "VALUE-MUST-NOT-BE-PRINTED"
NOW = datetime(2026, 10, 4, 14, 30, 5)
BACKUP_NAME = ".env.bak-20261004-143005"


def old_unprefixed(new: str) -> str:
    return next(
        old for old, target in RENAMED.items()
        if target == new and not old.startswith(OLD_PREFIX)
    )


OLD_MOSS = OLD_PREFIX + "MOSS_ENABLED"
OLD_CATALOG = OLD_PREFIX + "SAFETY_CATALOG"
OLD_PROFILES = OLD_PREFIX + "DEV_AUTH_PROFILES"
OLD_OCR = OLD_PREFIX + "OCR_PROVIDERS"
OLD_UNREAD = OLD_PREFIX + "NO_LONGER_READ"
OLD_CHAT = old_unprefixed("VOINEY_LAB_CHAT_MODEL")
OLD_VOICE = old_unprefixed("VOINEY_LAB_TTS_VOICE")

FAKE_ENV = (
    "# xAI 키 메모\n"
    f"XAI_API_KEY=sk-{SECRET}-1\n"
    f"{OLD_MOSS}=false\n"
    f'{OLD_CHAT}="grok 4 {SECRET}-2"  # inline note\n'
    f"export {OLD_VOICE}=leo\n"
    "\n"
    "# 떨어진 주석\n"
    "\n"
    f"UNRELATED_TOOL_TOKEN=keep {SECRET}-3\n"
    f"{OLD_CATALOG}=/absolute/{SECRET}-4/catalog.sqlite\n"
    "VOINEY_LAB_USAGE_SCOPE=demo\n"
    f"{OLD_PROFILES}='[{{\"id\": \"{SECRET}-5\"}},\n {{\"id\": \"b\"}}]'\n"
    f"{OLD_OCR}=clova\n"
    f"ANTHROPIC_API_KEY=sk-ant-{SECRET}-6\n"
    f"{OLD_UNREAD}=x={SECRET}-7\n"
    f"EMPTY_ONE=\n"
)


class MigrateEnvCase(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.folder = Path(self._temp.name)
        self.path = self.folder / ".env"

    def tearDown(self):
        self._temp.cleanup()

    def given(self, text: str, mode: int = 0o644) -> bytes:
        self.path.write_text(text, encoding="utf-8")
        self.path.chmod(mode)
        return self.path.read_bytes()

    def run_tool(self, *arguments: str, now: datetime = NOW) -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = migrate_env.main([*arguments, str(self.path)], now=now)
        return code, out.getvalue() + err.getvalue()

    def backups(self) -> list[str]:
        return sorted(path.name for path in self.folder.glob(".env.bak-*"))


class CheckTests(MigrateEnvCase):
    def test_check_lists_names_only_and_writes_nothing(self):
        original = self.given(FAKE_ENV)
        code, output = self.run_tool("--check")
        self.assertEqual(code, 1)
        for old in (OLD_MOSS, OLD_CHAT, OLD_VOICE, OLD_CATALOG, OLD_PROFILES, OLD_OCR):
            self.assertIn(f"  {old} → {renamed(old)}\n", output)
        stays = output.split("그대로인 이름")[1].split("코드가 읽지 않는 이름")[0]
        self.assertIn("  XAI_API_KEY\n", stays)
        self.assertIn("  VOINEY_LAB_USAGE_SCOPE\n", stays)
        unread = output.split("코드가 읽지 않는 이름")[1].split("중복")[0]
        for name in ("UNRELATED_TOOL_TOKEN", "ANTHROPIC_API_KEY", "EMPTY_ONE"):
            self.assertIn(f"  {name}\n", unread)
        self.assertIn(f"  {renamed(OLD_UNREAD)} (옛 이름 {OLD_UNREAD})\n", unread)
        self.assertIn("중복 (0개)", output)
        self.assertNotIn(SECRET, output)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.backups(), [])

    def test_check_passes_a_migrated_file(self):
        self.given(FAKE_ENV)
        self.assertEqual(self.run_tool("--write")[0], 0)
        code, output = self.run_tool("--check", now=datetime(2026, 10, 4, 15, 0, 0))
        self.assertEqual(code, 0, output)
        self.assertIn("바뀔 이름 (옛 → 새) (0개)", output)

    def test_command_line_prints_names_only(self):
        self.given(FAKE_ENV)
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--check", str(self.path)],
            capture_output=True, text=True, timeout=60, check=False,
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(f"{OLD_MOSS} → VOINEY_LAB_MOSS_ENABLED", result.stdout)
        self.assertNotIn(SECRET, result.stdout + result.stderr)
        missing = subprocess.run(
            [sys.executable, str(SCRIPT), "--check", str(self.folder / "absent.env")],
            capture_output=True, text=True, timeout=60, check=False,
        )
        self.assertEqual(missing.returncode, 2)


class WriteTests(MigrateEnvCase):
    def test_write_backs_up_the_original_byte_for_byte(self):
        original = self.given(FAKE_ENV)
        code, output = self.run_tool("--write")
        self.assertEqual(code, 0, output)
        self.assertEqual(self.backups(), [BACKUP_NAME])
        self.assertEqual((self.folder / BACKUP_NAME).read_bytes(), original)
        self.assertNotIn(SECRET, output)

    def test_values_are_kept_character_for_character(self):
        self.given(FAKE_ENV)
        before = dotenv_values(self.path)
        raw_before = {
            line.split("=", 1)[0].removeprefix("export "): line.split("=", 1)[1]
            for line in FAKE_ENV.splitlines() if "=" in line and not line.startswith(("#", " "))
        }
        self.assertEqual(self.run_tool("--write")[0], 0)
        after = dotenv_values(self.path)
        self.assertEqual(after, {renamed(name) or name: value for name, value in before.items()})
        written = self.path.read_text(encoding="utf-8")
        for name, raw in raw_before.items():
            with self.subTest(name=name):
                self.assertIn(f"{renamed(name) or name}={raw}", written)
        self.assertEqual([name for name in after if renamed(name)], [])

    def test_settings_are_grouped_by_area_sorted_and_commented(self):
        self.given(FAKE_ENV)
        self.assertEqual(self.run_tool("--write")[0], 0)
        lines = self.path.read_text(encoding="utf-8").splitlines()
        titles = [line[len("## ── "):-len(" ──")] for line in lines if line.startswith("## ── ")]
        self.assertEqual(titles, [
            "공급자 키", "OCR", "모델", "기능 켜기·끄기", "경로·저장소", "그 밖",
            "코드가 읽지 않는 설정", "옛 파일에서 옮긴 주석 (바로 아래에 설정이 없던 것)",
        ])
        self.assertEqual([title for title in titles if title in AREAS], list(AREAS))
        sections: dict[str, list[str]] = {}
        current = ""
        for line in lines:
            if line.startswith("## ── "):
                current = line
            elif "=" in line and not line.startswith(("#", " ")):
                sections.setdefault(current, []).append(
                    line.split("=", 1)[0].removeprefix("export "))
        for title, names in sections.items():
            with self.subTest(section=title):
                self.assertEqual(names, sorted(names))
        for index, line in enumerate(lines):
            name = line.split("=", 1)[0].removeprefix("export ")
            if name in BY_NAME and "=" in line:
                with self.subTest(name=name):
                    self.assertEqual(lines[index - 1], f"## {BY_NAME[name].meaning}")
        xai = next(i for i, line in enumerate(lines) if line.startswith("XAI_API_KEY="))
        self.assertEqual(lines[xai - 2], "# xAI 키 메모")
        self.assertEqual(lines[-1], "# 떨어진 주석")

    def test_names_the_code_does_not_read_move_to_the_bottom_unchanged(self):
        self.given(FAKE_ENV)
        self.assertEqual(self.run_tool("--write")[0], 0)
        text = self.path.read_text(encoding="utf-8")
        bottom = text.split("## ── 코드가 읽지 않는 설정 ──\n")[1]
        self.assertIn(f"UNRELATED_TOOL_TOKEN=keep {SECRET}-3\n", bottom)
        self.assertIn(f"ANTHROPIC_API_KEY=sk-ant-{SECRET}-6\n", bottom)
        self.assertIn("EMPTY_ONE=\n", bottom)
        self.assertIn(f"{renamed(OLD_UNREAD)}=x={SECRET}-7\n", bottom)
        for name in ("UNRELATED_TOOL_TOKEN", "ANTHROPIC_API_KEY", "EMPTY_ONE"):
            self.assertNotIn(f"{name}=", text.split("## ── 코드가 읽지 않는 설정 ──\n")[0])

    def test_running_it_again_changes_nothing(self):
        self.given(FAKE_ENV)
        self.assertEqual(self.run_tool("--write")[0], 0)
        first = self.path.read_bytes()
        self.assertEqual(self.run_tool("--write", now=datetime(2026, 10, 4, 15, 0, 0))[0], 0)
        self.assertEqual(self.path.read_bytes(), first)

    def test_new_file_and_backup_are_private(self):
        self.given(FAKE_ENV, mode=0o644)
        previous = os.umask(0)
        try:
            self.assertEqual(self.run_tool("--write")[0], 0)
        finally:
            os.umask(previous)
        for path in (self.path, self.folder / BACKUP_NAME):
            with self.subTest(path=path.name):
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), sorted([".env", BACKUP_NAME]))


class StopTests(MigrateEnvCase):
    def assert_stops_without_writing(self, text: str, *names: str) -> None:
        original = self.given(text)
        code, output = self.run_tool("--write")
        self.assertEqual(code, 2, output)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.backups(), [])
        self.assertNotIn(SECRET, output)
        for name in names:
            self.assertIn(name, output)

    def test_a_name_set_twice_stops(self):
        self.assert_stops_without_writing(
            f"VOINEY_LAB_USAGE_SCOPE=demo\nXAI_API_KEY={SECRET}\nVOINEY_LAB_USAGE_SCOPE=test_only\n",
            "VOINEY_LAB_USAGE_SCOPE",
        )

    def test_an_old_and_a_new_name_for_one_setting_stop(self):
        self.assert_stops_without_writing(
            f"{OLD_MOSS}=false\nVOINEY_LAB_MOSS_ENABLED={SECRET}\n",
            "VOINEY_LAB_MOSS_ENABLED", OLD_MOSS,
        )

    def test_two_old_names_that_became_one_stop(self):
        new = "VOINEY_LAB_EXTERNAL_REFERENCES_ENABLED"
        olds = sorted(old for old, target in RENAMED.items() if target == new)
        self.assertEqual(len(olds), 2)
        self.assert_stops_without_writing(
            f"{olds[0]}=true\n{olds[1]}={SECRET}\n", new, *olds,
        )
        code, output = self.run_tool("--check")
        self.assertEqual(code, 1)
        self.assertIn(f"{new}: {olds[0]} (1번째 줄), {olds[1]} (2번째 줄)", output)

    def test_a_line_that_is_not_dotenv_stops(self):
        self.assert_stops_without_writing(f"XAI_API_KEY={SECRET}\nthis is not a setting\n", "2번째 줄")


class SecretReferenceTests(MigrateEnvCase):
    def test_referenced_secrets_stay_with_the_provider_keys(self):
        self.given(
            f"{OLD_PREFIX}SECRET_REFERENCES={{\"secret://t/eln\":\"ELN_TOKEN\"}}\n"
            f"ELN_TOKEN={SECRET}\n"
        )
        code, output = self.run_tool("--write")
        self.assertEqual(code, 0, output)
        keys = self.path.read_text(encoding="utf-8").split("## ── 공급자 키 ──\n")[1]
        self.assertIn(f"ELN_TOKEN={SECRET}\n", keys.split("## ── ")[0])
        self.assertIn('VOINEY_LAB_SECRET_REFERENCES={"secret://t/eln":"ELN_TOKEN"}\n', keys)

    def test_a_reference_to_an_old_name_stops(self):
        old_token = OLD_PREFIX + "ELN_TOKEN"
        original = self.given(
            f"VOINEY_LAB_SECRET_REFERENCES={{\"secret://t/eln\":\"{old_token}\"}}\n"
            f"{old_token}={SECRET}\n"
        )
        code, output = self.run_tool("--write")
        self.assertEqual(code, 2, output)
        self.assertIn(old_token, output)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.backups(), [])
        self.assertEqual(self.run_tool("--check")[0], 1)


class ExampleFileTests(unittest.TestCase):
    def test_env_example_is_in_the_tools_layout(self):
        text = (ROOT / ".env.example").read_text(encoding="utf-8")
        parsed = migrate_env.parse(text)
        self.assertEqual(migrate_env.render(parsed), text)
        self.assertEqual(migrate_env.duplicates(parsed.entries), {})
        self.assertEqual([e.name for e in parsed.entries if renamed(e.name)], [])
        self.assertEqual(parsed.loose_comments, [])


if __name__ == "__main__":
    unittest.main()
