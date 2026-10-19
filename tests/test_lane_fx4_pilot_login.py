"""Lane FX4, decision 4 (2026-10-19): the pilot always signs in.

Human decision of 2026-10-19: ``scripts/run_pilot.sh`` starts the server with
accounts (``VOINEY_LAB_AUTH_MODE=accounts``) unless a person chooses the
development identity on the command line (``--dev-identity``), which the
launcher says with a ``[WARN]``. A value of the setting in the shell or the
repository .env does not choose it. ``--check-only`` says when the workspace
(which accounts need) is off and when the admin token is empty. The token's
value is never printed.

The launcher runs from a temporary copy whose ROOT is a temp dir, with a
stand-in ``python`` that refuses to serve (tests/test_launcher_settings_win.py).
"""

from __future__ import annotations

import stat
import sys
import unittest

from tests.test_launcher_settings_win import SERVE_MARKER
from tests.test_lane_pl_pilot_launcher import _PilotCopy

TOKEN = "lane-fx4-secret-admin-token"
DEV_WARNING = "[WARN] --dev-identity: no sign-in."
NO_TOKEN = "[WARN] VOINEY_LAB_ADMIN_TOKEN is empty."
NO_WORKSPACE = "[WARN] The workspace is off (VOINEY_LAB_WORKSPACE_ENABLED)."


class PilotLoginTests(_PilotCopy, unittest.TestCase):

    def run_check(self, *args: str, **extra: str):
        extra.setdefault("VOINEY_LAB_ADMIN_TOKEN", TOKEN)
        result = self._run("--check-only", *args, **extra)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def test_accounts_is_the_default(self) -> None:
        out = self.run_check()
        self.assertIn("AUTH_MODE      = accounts\n", out)
        self.assertNotIn(DEV_WARNING, out)
        self.assertIn("ADMIN_TOKEN    = set\n", out)
        self.assertNotIn(TOKEN, out)
        self.assertNotIn(NO_TOKEN, out)
        self.assertNotIn(NO_WORKSPACE, out)

    def test_the_development_identity_is_chosen_only_on_the_command_line(self) -> None:
        out = self.run_check("--dev-identity")
        self.assertIn("AUTH_MODE      = dev\n", out)
        self.assertIn(DEV_WARNING, out)

    def test_a_setting_in_the_shell_or_the_dotenv_does_not_choose_it(self) -> None:
        out = self.run_check(VOINEY_LAB_AUTH_MODE="dev")
        self.assertIn("AUTH_MODE      = accounts\n", out)
        self.assertIn("[WARN] VOINEY_LAB_AUTH_MODE was set to dev in the environment or .env.", out)
        self.assertNotIn(DEV_WARNING, out)
        self._dotenv("VOINEY_LAB_AUTH_MODE=dev\n")
        out = self.run_check()
        self.assertIn("AUTH_MODE      = accounts\n", out)
        self.assertIn("[WARN] VOINEY_LAB_AUTH_MODE was set to dev in the environment or .env.", out)
        # The same value as the launcher's is no news.
        out = self.run_check(VOINEY_LAB_AUTH_MODE="accounts")
        self.assertNotIn("[WARN] VOINEY_LAB_AUTH_MODE", out)

    def test_an_empty_admin_token_is_said(self) -> None:
        out = self.run_check(VOINEY_LAB_ADMIN_TOKEN="")
        self.assertIn("ADMIN_TOKEN    = empty\n", out)
        self.assertIn(NO_TOKEN, out)
        # A token written in the .env counts, as the server reads it.
        self._dotenv(f"VOINEY_LAB_ADMIN_TOKEN={TOKEN}\n")
        result = self._run("--check-only", **{"VOINEY_LAB_ADMIN_TOKEN": ""})
        self.assertIn(NO_TOKEN, result.stdout)  # the shell's empty value wins (override=False)
        environment = {key: value for key, value in self._environment().items()
                       if key != "VOINEY_LAB_ADMIN_TOKEN"}
        out = self._run_with(environment, "--check-only").stdout
        self.assertIn("ADMIN_TOKEN    = set\n", out)
        self.assertNotIn(TOKEN, out)

    def test_a_workspace_that_is_off_is_said(self) -> None:
        script = self.root / "scripts" / self.SCRIPT
        text = script.read_text(encoding="utf-8")
        line = 'export VOINEY_LAB_WORKSPACE_ENABLED="true"'
        self.assertIn(line, text)
        script.write_text(text.replace(line, 'export VOINEY_LAB_WORKSPACE_ENABLED="false"'),
                          encoding="utf-8")
        out = self.run_check()
        self.assertIn(NO_WORKSPACE, out)

    def test_the_server_is_handed_the_door(self) -> None:
        dump = self.root / "server-env.txt"
        python = self.root / ".venv" / "bin" / "python"
        python.write_text(
            "#!/bin/sh\n"
            f'case " $* " in *" uvicorn "*) env | grep "^VOINEY_LAB_AUTH_MODE=" > "{dump}"; '
            f'echo "{SERVE_MARKER}" >&2; exit 97;; esac\n'
            f'exec "{sys.executable}" "$@"\n',
            encoding="utf-8",
        )
        python.chmod(python.stat().st_mode | stat.S_IXUSR)
        for args, mode in (((), "accounts"), (("--dev-identity",), "dev")):
            with self.subTest(mode=mode):
                result = self._run(*args, VOINEY_LAB_AUTH_MODE="dev" if mode == "accounts" else "")
                self.assertEqual(result.returncode, 97, result.stdout + result.stderr)
                self.assertEqual(dump.read_text(encoding="utf-8"), f"VOINEY_LAB_AUTH_MODE={mode}\n")

    def test_an_unknown_option_is_refused(self) -> None:
        result = self._run("--dev")
        self.assertEqual(result.returncode, 2)
        self.assertIn("--dev-identity", result.stdout)

    # -- helpers -------------------------------------------------------------

    def _environment(self) -> dict[str, str]:
        from tests.test_launcher_settings_win import _clean_environment

        return _clean_environment()

    def _run_with(self, environment: dict[str, str], *args: str):
        import subprocess

        return subprocess.run(
            ["bash", str(self.root / "scripts" / self.SCRIPT), *args],
            cwd=self.root, env=environment, text=True, capture_output=True, timeout=120,
            check=False,
        )


if __name__ == "__main__":
    unittest.main()
