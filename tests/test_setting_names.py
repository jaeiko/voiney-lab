"""The setting name table matches the code, and no old name is left.

Decision of 2026-10-04: every setting the code reads carries the VOINEY_LAB_
prefix, one table (``voiney_lab.setting_names``) lists them all, and the only
place an old name may still be spelled is the old -> new map the migration
tool uses (``voiney_lab.setting_renames``).
"""

from __future__ import annotations

import ast
import re
import subprocess
import unittest
from pathlib import Path

from voiney_lab.setting_names import AREAS, BY_NAME, EXTERNAL_NAMES, SETTINGS
from voiney_lab.setting_renames import NEW_PREFIX, OLD_PREFIX, RENAMED, renamed

ROOT = Path(__file__).resolve().parents[1]
TABLE_FILES = {
    "src/voiney_lab/setting_names.py",
    "src/voiney_lab/setting_renames.py",
}
NAME = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$")
PREFIXED = re.compile(re.escape(NEW_PREFIX) + r"[A-Z0-9_]*[A-Z0-9]")


def repository_files() -> list[str]:
    """Tracked and new (not ignored) files, as git sees them."""

    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    return sorted({path for path in listed if (ROOT / path).is_file()})


def text_of(path: str) -> str | None:
    raw = (ROOT / path).read_bytes()
    if b"\0" in raw:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def is_code(path: str) -> bool:
    """Files that read settings: the package, scripts, CI, the Playwright config."""

    if path in TABLE_FILES:
        return False
    return (
        path.startswith(("src/", "scripts/", ".github/workflows/"))
        or path.startswith("playwright") and path.endswith(".ts")
    )


# Where the Python code reads a setting: os.environ / env / environment
# lookups, the small bounded-value helpers that take the name, and module
# constants named *_ENV.
_ENV_OBJECTS = {"environ", "env", "environment"}
_NAME_HELPERS = {
    "require_env", "_integer", "_floating", "_flag", "_bounded_float",
    "_bounded_int", "_enabled", "_boolean_env", "_aliased_value",
    "_aliased_enabled", "bounded_float",
}


def _is_environment(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return node.id in _ENV_OBJECTS
    return isinstance(node, ast.Attribute) and node.attr in _ENV_OBJECTS


def python_setting_reads(tree: ast.AST):
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id.endswith("_ENV")
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            yield node.lineno, node.value.value
        elif isinstance(node, ast.Call):
            func = node.func
            arguments: list[ast.expr] = []
            if (
                isinstance(func, ast.Attribute)
                and func.attr in {"get", "pop", "setdefault"}
                and _is_environment(func.value)
            ) or isinstance(func, ast.Attribute) and func.attr == "getenv":
                arguments = node.args[:1]
            elif isinstance(func, ast.Name) and func.id in _NAME_HELPERS:
                arguments = list(node.args)
            for argument in arguments:
                if (
                    isinstance(argument, ast.Constant)
                    and isinstance(argument.value, str)
                    and NAME.match(argument.value)
                ):
                    yield node.lineno, argument.value
        elif isinstance(node, ast.Subscript) and _is_environment(node.value):
            if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
                yield node.lineno, node.slice.value


class SettingTableTests(unittest.TestCase):
    def test_table_rows_are_unique_grouped_and_prefixed(self):
        names = [setting.name for setting in SETTINGS]
        self.assertEqual(len(names), len(set(names)))
        for setting in SETTINGS:
            with self.subTest(name=setting.name):
                self.assertIn(setting.area, AREAS)
                self.assertTrue(setting.meaning.strip())
                self.assertTrue(setting.default.strip())
                if setting.name.startswith(NEW_PREFIX):
                    self.assertEqual(setting.kept_because, "")
                else:
                    self.assertTrue(setting.kept_because.strip())
                self.assertIsNone(renamed(setting.name))
        for name, (area, reason) in EXTERNAL_NAMES.items():
            with self.subTest(external=name):
                self.assertNotIn(name, BY_NAME)
                self.assertIsNone(renamed(name))
                self.assertTrue(area is None or area in AREAS)
                self.assertTrue(reason.strip())

    def test_every_old_name_maps_to_a_table_name(self):
        for old, new in RENAMED.items():
            with self.subTest(old=old):
                self.assertIn(new, BY_NAME)
                self.assertTrue(new.startswith(NEW_PREFIX))
                rest = old[len(OLD_PREFIX):] if old.startswith(OLD_PREFIX) else old
                # Lane SV (human decision 2026-10-05): the xAI-only STT names
                # moved to provider-neutral ones, so for them the new name is
                # the old one with XAI_STT_ made STT_, not just re-prefixed.
                rest = rest.removeprefix(NEW_PREFIX)
                if rest.startswith("XAI_STT_"):
                    rest = rest[len("XAI_"):]
                self.assertEqual(new, NEW_PREFIX + rest)
        # A setting added after the rename has no old name, so the renamed
        # names are a subset of the table rather than all of it (human
        # decision 2026-10-05, lane P2).
        prefixed = {name for name in BY_NAME if name.startswith(NEW_PREFIX)}
        self.assertLessEqual(set(RENAMED.values()), prefixed)

    def test_code_spells_exactly_the_table_names(self):
        spelled: dict[str, str] = {}
        for path in filter(is_code, repository_files()):
            text = text_of(path)
            for name in PREFIXED.findall(text or ""):
                spelled.setdefault(name, path)
        table = {name for name in BY_NAME if name.startswith(NEW_PREFIX)}
        self.assertEqual(
            {name: spelled[name] for name in set(spelled) - table}, {},
            "the code spells a setting the table does not list",
        )
        self.assertEqual(
            sorted(table - set(spelled)), [],
            "the table lists a setting the code never spells",
        )

    def test_kept_names_are_read_by_the_code(self):
        code = "\n".join(
            text_of(path) or "" for path in filter(is_code, repository_files())
        )
        for setting in SETTINGS:
            if setting.name.startswith(NEW_PREFIX):
                continue
            with self.subTest(name=setting.name):
                self.assertRegex(
                    code, r"(?<![A-Za-z0-9_])" + setting.name + r"(?![A-Za-z0-9_])"
                )

    def test_every_python_setting_read_is_a_table_name(self):
        unknown = []
        for path in filter(is_code, repository_files()):
            if not path.endswith(".py"):
                continue
            tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
            for line, name in python_setting_reads(tree):
                if name not in BY_NAME:
                    unknown.append(f"{path}:{line} {name}")
        self.assertEqual(unknown, [])

    def test_tests_and_documents_name_only_table_settings(self):
        stray = []
        for path in repository_files():
            if is_code(path) or path in TABLE_FILES:
                continue
            for name in set(PREFIXED.findall(text_of(path) or "")):
                if name not in BY_NAME:
                    stray.append(f"{path}: {name}")
        self.assertEqual(sorted(stray), [])


class NoOldNameTests(unittest.TestCase):
    def test_no_old_setting_name_is_left_outside_the_rename_map(self):
        unprefixed = sorted(
            (name for name in RENAMED if not name.startswith(OLD_PREFIX)),
            key=len, reverse=True,
        )
        old = re.compile(
            re.escape(OLD_PREFIX)
            + r"|(?<![A-Za-z0-9_])(?:" + "|".join(unprefixed) + r")(?![A-Za-z0-9_])"
        )
        found = []
        for path in repository_files():
            if path == "src/voiney_lab/setting_renames.py":
                continue
            text = text_of(path)
            if text is None:
                continue
            for number, line in enumerate(text.splitlines(), 1):
                match = old.search(line)
                if match:
                    found.append(f"{path}:{number}: {match.group(0)}")
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()
