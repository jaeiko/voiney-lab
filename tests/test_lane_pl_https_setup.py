"""Lane PL, stage 2d: the HTTPS preparation for the pilot (written, never run here).

scripts/pilot_https_setup.sh puts Caddy, HTTPS and a shared password in front of
the pilot server (a phone browser uses the microphone only over HTTPS). The
domain is not decided and lane PL ran nothing: these tests run the script only
with --dry-run, or with arguments it refuses before it touches anything, and
check the Caddyfile example. No password, hash or domain of a real deployment is
in the repository.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "pilot_https_setup.sh"
EXAMPLE = ROOT / "scripts" / "pilot_https" / "Caddyfile.example"
HASH = "$2a$14$" + "a" * 53  # bcrypt-shaped, not a real hash of anything


def run(*args: str, caddyfile: Path | None = None, extra_env: dict[str, str] | None = None):
    env = {key: value for key, value in os.environ.items() if not key.startswith("PILOT_HTTPS_")}
    if caddyfile is not None:
        env["PILOT_HTTPS_CADDYFILE"] = str(caddyfile)
    env.update(extra_env or {})
    return subprocess.run(
        ["bash", str(SCRIPT), *args], env=env, text=True, capture_output=True,
        timeout=60, check=False, stdin=subprocess.DEVNULL,
    )


class SyntaxAndExampleTests(unittest.TestCase):
    def test_the_script_parses_and_is_executable(self):
        self.assertEqual(subprocess.run(["bash", "-n", str(SCRIPT)]).returncode, 0)
        self.assertTrue(os.access(SCRIPT, os.X_OK))

    def test_the_example_has_only_placeholders_and_the_three_things_it_is_for(self):
        text = EXAMPLE.read_text(encoding="utf-8")
        self.assertEqual(
            sorted(set(re.findall(r"\{\{(\w+)\}\}", text))),
            ["DOMAIN", "PASSWORD_HASH", "UPSTREAM", "USER"])
        self.assertIn("basic_auth", text)
        self.assertIn("reverse_proxy {{UPSTREAM}}", text)
        # No real hash, password or domain is written in the example.
        self.assertIsNone(re.search(r"\$2[aby]\$", text))
        self.assertNotIn("log {", text)  # no access log: request paths hold protocol ids

    def test_nothing_in_the_repository_names_a_password_or_a_real_domain_for_the_pilot(self):
        text = SCRIPT.read_text(encoding="utf-8") + EXAMPLE.read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"(?i)password\s*=\s*\S", text))
        # The only address the script reaches is Caddy's own package source.
        hosts = sorted(set(re.findall(r"https?://([^/\s'\"]+)", text)))
        self.assertEqual(hosts, ["$DOMAIN", "dl.cloudsmith.io"])  # the user's own domain, and Caddy's package source


class DryRunTests(unittest.TestCase):
    def test_a_dry_run_prints_the_caddyfile_with_the_hash_hidden_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "Caddyfile"
            result = run(
                "--domain", "Pilot.Example.ORG", "--user", "bench", "--upstream", "127.0.0.1:9090",
                "--password-hash", HASH, "--dry-run", caddyfile=target)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertFalse(target.exists())
            self.assertEqual(list(Path(temp).iterdir()), [])
        out = result.stdout
        self.assertIn("pilot.example.org {", out)  # lower-cased
        self.assertIn("bench <bcrypt-hash>", out)
        self.assertIn("reverse_proxy 127.0.0.1:9090", out)
        self.assertIn("basic_auth {", out)
        self.assertNotIn("{{", out)
        self.assertNotIn(HASH, out + result.stderr)
        self.assertIn("아무것도 설치하거나 바꾸지 않습니다", out)
        self.assertIn("주어진 해시를 쓴다", out)

    def test_a_dry_run_needs_no_root_no_terminal_and_no_password(self):
        result = run("--domain", "pilot.example.org", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("비밀번호를 두 번 묻고", result.stdout)
        self.assertIn("reverse_proxy 127.0.0.1:8080", result.stdout)

    def test_the_password_is_never_taken_as_an_argument(self):
        for flag in ("--password", "--pass"):
            result = run("--domain", "pilot.example.org", flag, "secret", "--dry-run")
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assertIn("usage:", result.stderr)


class RefusalTests(unittest.TestCase):
    """Refused before anything is installed, written or asked."""

    def test_a_missing_domain_is_a_usage_error(self):
        result = run()
        self.assertEqual(result.returncode, 2)
        self.assertIn("usage:", result.stderr)

    def test_a_malformed_value_is_refused_by_name(self):
        for arguments, name in (
            (("--domain", "not a domain"), "--domain"),
            (("--domain", "localhost"), "--domain"),
            (("--domain", "pilot.example.org", "--user", "bad user"), "--user"),
            (("--domain", "pilot.example.org", "--user", "x" * 40), "--user"),
            (("--domain", "pilot.example.org", "--upstream", "no-port"), "--upstream"),
            (("--domain", "pilot.example.org", "--upstream", "a b:80"), "--upstream"),
            (("--domain", "pilot.example.org", "--password-hash", "plaintext"), "--password-hash"),
            (("--domain", "pilot.example.org", "--password-hash", HASH[:-3]), "--password-hash"),
        ):
            with self.subTest(arguments=arguments):
                result = run(*arguments, "--dry-run")
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(name, result.stderr)

    def test_an_injection_in_a_value_never_reaches_the_caddyfile(self):
        for value in ("pilot.example.org }\nhandle { respond 200", "pilot.example.org;rm -rf /"):
            with self.subTest(value=value):
                result = run("--domain", value, "--dry-run")
                self.assertEqual(result.returncode, 1)
                self.assertNotIn("respond", result.stdout)

    def test_a_real_run_with_the_example_domain_is_refused_before_anything_happens(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "Caddyfile"
            result = run("--domain", "pilot.example.org", "--password-hash", HASH, caddyfile=target)
            self.assertEqual(result.returncode, 1)
            self.assertIn("예시 도메인", result.stderr)
            self.assertFalse(target.exists())

    @unittest.skipIf(os.getuid() == 0, "as root a real run would go on to install; never run it here")
    def test_a_real_run_without_root_stops_before_installing_or_writing(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "Caddyfile"
            result = run("--domain", "pilot.real-domain.test", "--password-hash", HASH, caddyfile=target)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("root 권한이 필요합니다", result.stderr)
            self.assertFalse(target.exists())


@unittest.skipUnless(shutil.which("caddy"), "caddy is not installed here")
class CaddyAcceptsTheExampleTests(unittest.TestCase):
    def test_caddy_validates_what_a_dry_run_prints(self):
        result = run("--domain", "pilot.example.org", "--password-hash", HASH, "--dry-run")
        body = result.stdout.split("--- 만들 Caddyfile (해시는 가린다) ---\n", 1)[1]
        body = body.replace("<bcrypt-hash>", HASH)
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "Caddyfile"
            config.write_text(body, encoding="utf-8")
            checked = subprocess.run(
                ["caddy", "validate", "--config", str(config), "--adapter", "caddyfile"],
                text=True, capture_output=True, timeout=60)
        self.assertEqual(checked.returncode, 0, checked.stderr)


if __name__ == "__main__":
    unittest.main()
