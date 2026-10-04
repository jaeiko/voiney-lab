"""An old setting name in the environment stops the server, launchers and tests.

Decision of 2026-10-04 (decision 3): there is no transition period in which an
old name is read. Instead every entry point fails closed: it refuses to start,
says how many old names it found and which -- the names only, never a value --
and points at scripts/migrate_env.py. The old names are taken from the rename
map, so this file spells none of them.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from voiney_lab.setting_names import (
    OldSettingNamesError,
    old_names_message,
    refuse_old_setting_names,
)
from voiney_lab.setting_renames import OLD_PREFIX, RENAMED

ROOT = Path(__file__).resolve().parents[1]
OLD_PREFIXED = OLD_PREFIX + "MOSS_ENABLED"
OLD_UNPREFIXED = next(old for old, new in RENAMED.items() if new == "VOINEY_LAB_CHAT_MODEL")
SECRET = "VALUE-MUST-NOT-BE-PRINTED-7f3a"
GUIDANCE = "scripts/migrate_env.py 를 실행하세요."


def clean_environment(**extra: str) -> dict[str, str]:
    """The test process environment (already free of old names) plus ``extra``."""

    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(extra)
    return env


class GuardMessageTests(unittest.TestCase):
    def test_message_counts_and_names_without_values(self):
        with self.assertRaises(OldSettingNamesError) as caught:
            refuse_old_setting_names({
                OLD_UNPREFIXED: SECRET,
                "VOINEY_LAB_USAGE_SCOPE": "demo",
                OLD_PREFIXED: SECRET,
            })
        message = str(caught.exception)
        self.assertEqual(
            message,
            f"옛 설정 이름 2개: {', '.join(sorted([OLD_UNPREFIXED, OLD_PREFIXED]))}. "
            + GUIDANCE,
        )
        self.assertNotIn(SECRET, message)

    def test_new_and_kept_names_pass(self):
        refuse_old_setting_names({
            "VOINEY_LAB_MOSS_ENABLED": "false",
            "VOINEY_LAB_CHAT_MODEL": "grok-4",
            "XAI_API_KEY": SECRET,
            "HOST": "127.0.0.1",
            "PORT": "8000",
            "UNRELATED_TOOL_SETTING": "x",
        })

    def test_a_prefixed_name_the_map_does_not_list_is_still_old(self):
        with self.assertRaises(OldSettingNamesError) as caught:
            refuse_old_setting_names({OLD_PREFIX + "NOT_A_SETTING_ANY_MORE": SECRET})
        self.assertIn(OLD_PREFIX + "NOT_A_SETTING_ANY_MORE", str(caught.exception))

    def test_names_in_the_dotenv_count_too(self):
        with tempfile.TemporaryDirectory() as temporary:
            dotenv = Path(temporary) / ".env"
            refuse_old_setting_names({}, dotenv_path=dotenv)  # no file: nothing to refuse
            dotenv.write_text(f"VOINEY_LAB_USAGE_SCOPE=demo\n{OLD_PREFIXED}={SECRET}\n",
                              encoding="utf-8")
            with self.assertRaises(OldSettingNamesError) as caught:
                refuse_old_setting_names({}, dotenv_path=dotenv)
        self.assertEqual(str(caught.exception), old_names_message([OLD_PREFIXED]))


class EntryPointTests(unittest.TestCase):
    """The server, the worker, the launchers' check and pytest itself."""

    def assert_refused(self, result: subprocess.CompletedProcess, *names: str) -> None:
        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn(old_names_message(sorted(names)), output)
        self.assertNotIn(SECRET, output)

    def run_python(self, *arguments: str, **extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, *arguments], cwd=ROOT, env=clean_environment(**extra),
            text=True, capture_output=True, timeout=120, check=False,
        )

    def test_server_refuses_to_import(self):
        result = self.run_python("-c", "import voiney_lab.server",
                                 **{OLD_PREFIXED: SECRET})
        self.assert_refused(result, OLD_PREFIXED)
        self.assertIn("OldSettingNamesError", result.stderr)

    def test_worker_refuses_to_import(self):
        result = self.run_python("-c", "import voiney_lab.worker",
                                 **{OLD_UNPREFIXED: SECRET})
        self.assert_refused(result, OLD_UNPREFIXED)

    def test_launcher_check_module(self):
        refused = self.run_python("-B", "-m", "voiney_lab.setting_names",
                                  **{OLD_PREFIXED: SECRET, OLD_UNPREFIXED: SECRET})
        self.assertEqual(refused.returncode, 1)
        self.assert_refused(refused, OLD_PREFIXED, OLD_UNPREFIXED)
        passed = self.run_python("-B", "-m", "voiney_lab.setting_names")
        self.assertEqual((passed.returncode, passed.stdout, passed.stderr), (0, "", ""))

    def test_pytest_refuses_to_run(self):
        node = (
            "tests/test_old_setting_names_guard.py::GuardMessageTests::"
            "test_new_and_kept_names_pass"
        )
        result = self.run_python("-m", "pytest", "-q", "-p", "no:cacheprovider", node,
                                 **{OLD_UNPREFIXED: SECRET})
        self.assertEqual(result.returncode, 4, result.stdout + result.stderr)
        self.assert_refused(result, OLD_UNPREFIXED)
        self.assertNotIn("passed", result.stdout)


class LauncherTests(unittest.TestCase):
    """Each launcher, run from a copy under a temp root, stops at the check.

    As in tests/test_pilot_safety.py, a stand-in .venv puts the test
    interpreter first on PATH and refuses to run uvicorn, so a launcher that
    wrongly carries on fails here instead of starting a server.
    """

    SERVE_MARKER = "stand-in python: the launcher tried to serve"
    LAUNCHERS = ("run_dev.sh", "run_pilot.sh", "run_ci_server.sh", "collect_chunks.sh")

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        (self.root / "scripts").mkdir()
        for name in self.LAUNCHERS:
            shutil.copy2(ROOT / "scripts" / name, self.root / "scripts" / name)
        bin_dir = self.root / ".venv" / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "activate").write_text(f'export PATH="{bin_dir}:$PATH"\n', encoding="utf-8")
        python = bin_dir / "python"
        python.write_text(
            "#!/bin/sh\n"
            f'case " $* " in *" uvicorn "*) echo "{self.SERVE_MARKER}" >&2; exit 97;; esac\n'
            f'exec "{sys.executable}" "$@"\n',
            encoding="utf-8",
        )
        python.chmod(0o755)
        self.source = self.root / "source.pdf"
        self.source.write_bytes(b"%PDF-1.4\n")

    def tearDown(self):
        self._temp.cleanup()

    def test_every_launcher_refuses_before_it_does_anything(self):
        for name in self.LAUNCHERS:
            arguments = [str(self.source), "0"] if name == "collect_chunks.sh" else []
            with self.subTest(launcher=name):
                env = clean_environment(**{OLD_PREFIXED: SECRET, "HOST": "127.0.0.1",
                                           "PORT": "0"})
                env.pop("VIRTUAL_ENV", None)
                result = subprocess.run(
                    ["bash", str(self.root / "scripts" / name), *arguments],
                    cwd=self.root, env=env, text=True, capture_output=True,
                    timeout=120, check=False,
                )
                output = result.stdout + result.stderr
                self.assertEqual(result.returncode, 1, output)
                self.assertIn(old_names_message([OLD_PREFIXED]), output)
                self.assertNotIn(SECRET, output)
                self.assertNotIn(self.SERVE_MARKER, output)
                self.assertNotIn("===", result.stdout)
                self.assertFalse((self.root / "data").exists())


if __name__ == "__main__":
    unittest.main()
