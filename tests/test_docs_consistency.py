"""The documents point at things that exist (lane DOC4, decision 3).

Every Markdown document outside ``docs/archive/`` and ``docs/course-archive/``
is read the way a newcomer or a coding agent would follow it, and four things
are checked against the repository as it is:

1. a relative link points at a file or folder that is there;
2. a ``VOINEY_LAB_`` setting name is a row of ``voiney_lab.setting_names``;
3. a repository path written in code (``src/...``, ``scripts/...`` and the
   other top-level folders) is there -- from the root, beside the document
   that names it, or in the folder a ``cd <folder> &&`` on the same line moves
   to; a path git ignores is made at run time and is not checked;
4. a long option written after a ``scripts/`` command is one that script
   takes: a Python script's own ``--help``, or a shell script's ``case``
   patterns (running a launcher to ask it is not safe in a test).

A document that falls behind the code then fails here instead of misleading
the next reader. ``docs/demo_script.md`` belongs to a person and is never read.

One more check guards the agents' contract itself: Claude Code reads only
``CLAUDE.md`` at launch and gets ``AGENTS.md`` through its ``@AGENTS.md``
import line, so that line (outside code, where an import is ignored) and the
file it names must both be there.
"""

from __future__ import annotations

import functools
import re
import subprocess
import sys
import unittest
from pathlib import Path, PurePosixPath

from voiney_lab.setting_names import BY_NAME

ROOT = Path(__file__).resolve().parents[1]

#: Superseded or inherited records: read as history, never checked.
SKIPPED_FOLDERS = ("docs/archive/", "docs/course-archive/")
#: User-owned and untracked (AGENTS.md, "File ownership"); never read, never edited.
USER_OWNED = {"docs/demo_script.md"}
#: The standard test command switches this flag off although no code reads it
#: any more -- the same exception as tests/test_setting_names.py.
SPELLED_IN_TEST_COMMANDS = {"VOINEY_LAB_MOSS_ENABLED"}
#: Schema and setting history: it names files and settings that were deleted,
#: which is its job. Links and script options are still checked there.
HISTORY_DOCUMENTS = {"docs/MIGRATION_NOTES.md"}

TOP_FOLDERS = ("src", "scripts", "tests", "docs", "mobile", "data", ".agent", ".github")

FENCE = re.compile(r"^(```|~~~).*?^\1\s*$", re.M | re.S)
INLINE_CODE = re.compile(r"`([^`\n]+)`")
LINK = re.compile(r"(?<!!)\[[^\]\n]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
IMAGE = re.compile(r"!\[[^\]\n]*\]\(\s*<?([^)\s>]+)>?\s*\)")
REFERENCE_LINK = re.compile(r"^\s*\[[^\]\n]+\]:\s*<?(\S+?)>?\s*$", re.M)
SETTING = re.compile(r"VOINEY_LAB_[A-Z0-9_]*[A-Z0-9](\*)?")
PATH = re.compile(
    r"(?<![\w./-])(?:\./)?((?:" + "|".join(re.escape(folder) for folder in TOP_FOLDERS)
    + r")/[\w./@+*<>{}-]*)"
)
CHANGE_FOLDER = re.compile(r"\bcd\s+([\w./-]+)\s*&&")
PLACEHOLDER = re.compile(r"[<>{}*…$]|\.\.\.|XXX|YYYY")
SCRIPT_COMMAND = re.compile(r"(?<![\w/-])(?:\./)?scripts/([\w-]+\.(?:py|sh))\b([^\n|;&#`]*)")
LONG_OPTION = re.compile(r"(?<![\w-])(--[a-z][a-z0-9-]*)")
SHELL_CASE_OPTION = re.compile(r"^\s*((?:-{1,2}[\w-]+\|?)+)\)", re.M)


def repository_files() -> list[str]:
    """Tracked and new (not ignored) files, as git sees them."""

    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    return sorted({path for path in listed if (ROOT / path).is_file()})


@functools.cache
def _files_and_folders() -> frozenset[str]:
    entries: set[str] = set()
    for path in repository_files():
        entries.add(path)
        parent = PurePosixPath(path).parent
        while str(parent) != ".":
            entries.add(str(parent))
            parent = parent.parent
    return frozenset(entries)


def exists_in_repository(path: str) -> bool:
    return path.rstrip("/") in _files_and_folders()


@functools.cache
def ignored_by_git(path: str) -> bool:
    """Made by a build or a running server (``data/runtime/``, ``mobile/www/``)."""

    return any(
        subprocess.run(
            ["git", "check-ignore", "-q", "--no-index", candidate], cwd=ROOT,
        ).returncode == 0
        for candidate in (path, path.rstrip("/") + "/")
    )


def documents() -> list[str]:
    return [
        path for path in repository_files()
        if path.endswith(".md")
        and not path.startswith(SKIPPED_FOLDERS)
        and path not in USER_OWNED
        and path.startswith(("docs/", ".agent/", "mobile/")) or path in {
            "README.md", "AGENTS.md", "CLAUDE.md",
        }
    ]


def text_of(document: str) -> str:
    return (ROOT / document).read_text(encoding="utf-8")


def code_pieces(text: str) -> list[str]:
    """Fenced blocks and inline code spans: where paths and commands are written."""

    blocks = [match.group(0) for match in FENCE.finditer(text)]
    prose = FENCE.sub("", text)
    return blocks + INLINE_CODE.findall(prose)


def link_targets(text: str) -> list[str]:
    prose = INLINE_CODE.sub("", FENCE.sub("", text))
    return LINK.findall(prose) + IMAGE.findall(prose) + REFERENCE_LINK.findall(prose)


def _clean_path(raw: str) -> str:
    path = raw.split("::", 1)[0]
    path = re.sub(r":\d+(?:[-–]\d+)?$", "", path)
    return path.rstrip(".,:;)'\"")


@functools.cache
def script_options(script: str, subcommand: str | None) -> frozenset[str] | str:
    """The long options ``scripts/<script>`` takes, or why they could not be read."""

    path = ROOT / "scripts" / script
    if not path.is_file():
        return f"scripts/{script} is not in the repository"
    if script.endswith(".sh"):
        options: set[str] = set()
        for pattern in SHELL_CASE_OPTION.findall(path.read_text(encoding="utf-8")):
            options.update(option for option in pattern.split("|") if option.startswith("--"))
        return frozenset(options)
    command = [sys.executable, str(path)] + ([subcommand] if subcommand else []) + ["--help"]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        tail = (result.stderr or result.stdout).strip().splitlines()[-1:] or ["no output"]
        return f"`{' '.join(command[1:])}` exited {result.returncode}: {tail[0]}"
    return frozenset(LONG_OPTION.findall(result.stdout))


@functools.cache
def subcommands(script: str) -> frozenset[str]:
    """The subcommands a Python script lists as ``{a,b,c}`` in its ``--help``."""

    if not script.endswith(".py") or not (ROOT / "scripts" / script).is_file():
        return frozenset()
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script), "--help"],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
    )
    names: set[str] = set()
    for choice in re.findall(r"\{([\w,-]+)\}", result.stdout):
        names.update(choice.split(","))
    return frozenset(names)


class DocumentSetTests(unittest.TestCase):
    def test_the_documents_are_found_and_the_archives_are_not(self):
        found = documents()
        for expected in ("README.md", "AGENTS.md", "CLAUDE.md", "docs/DOCUMENTATION_INDEX.md"):
            self.assertIn(expected, found)
        self.assertFalse([path for path in found if path.startswith(SKIPPED_FOLDERS)])
        self.assertNotIn("docs/demo_script.md", found)


class LinkTests(unittest.TestCase):
    def test_every_relative_link_points_at_something_that_is_there(self):
        broken = []
        for document in documents():
            folder = PurePosixPath(document).parent
            for target in link_targets(text_of(document)):
                if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I) or target.startswith("#"):
                    continue
                relative = target.split("#", 1)[0].split("?", 1)[0]
                if not relative:
                    continue
                resolved = PurePosixPath(*(folder / relative).parts)
                parts: list[str] = []
                for part in resolved.parts:
                    if part == "..":
                        if parts:
                            parts.pop()
                    elif part != ".":
                        parts.append(part)
                if not exists_in_repository("/".join(parts)):
                    broken.append(f"{document}: ({target})")
        self.assertEqual(broken, [])


class SettingNameTests(unittest.TestCase):
    def test_every_setting_name_is_a_row_of_the_table(self):
        unknown = []
        for document in documents():
            for match in SETTING.finditer(text_of(document)):
                name = match.group(0)
                if name.endswith("*"):
                    prefix = name.rstrip("*")
                    if not any(row.startswith(prefix) for row in BY_NAME):
                        unknown.append(f"{document}: {name}")
                elif name not in BY_NAME and name not in SPELLED_IN_TEST_COMMANDS:
                    unknown.append(f"{document}: {name}")
        self.assertEqual(sorted(set(unknown)), [])


class PathTests(unittest.TestCase):
    def test_every_repository_path_written_in_code_is_there(self):
        missing = []
        for document in documents():
            if document in HISTORY_DOCUMENTS:
                continue
            for piece in code_pieces(text_of(document)):
                for line in piece.splitlines():
                    # "cd mobile && node scripts/x.mjs": read the path in that folder too.
                    folders = [PurePosixPath(document).parent] + [
                        PurePosixPath(folder) for folder in CHANGE_FOLDER.findall(line)
                    ]
                    for raw in PATH.findall(line):
                        path = _clean_path(raw)
                        if (
                            path in USER_OWNED
                            or PLACEHOLDER.search(path)
                            or path.rstrip("/") in TOP_FOLDERS
                            or ignored_by_git(path)
                        ):
                            continue
                        if not exists_in_repository(path) and not any(
                            exists_in_repository(str(folder / path)) for folder in folders
                        ):
                            missing.append(f"{document}: {path}")
        self.assertEqual(sorted(set(missing)), [])


#: An import line as Claude Code reads it: "@path" alone on a line, or after a
#: list marker. Inside backticks or a fenced block it is not an import.
IMPORT_LINE = re.compile(r"^\s*(?:[-*]\s+)?@(\S+)\s*$", re.M)


class AgentContractTests(unittest.TestCase):
    def test_claude_md_imports_agents_md_and_agents_md_is_there(self):
        claude = ROOT / "CLAUDE.md"
        self.assertTrue(claude.is_file(), "CLAUDE.md is missing")
        outside_code = INLINE_CODE.sub("", FENCE.sub("", claude.read_text(encoding="utf-8")))
        self.assertIn("AGENTS.md", IMPORT_LINE.findall(outside_code))
        self.assertTrue((ROOT / "AGENTS.md").is_file(), "AGENTS.md is missing")

    def test_an_import_inside_code_does_not_count(self):
        written = "`@AGENTS.md`\n```\n@AGENTS.md\n```\n"
        outside_code = INLINE_CODE.sub("", FENCE.sub("", written))
        self.assertEqual(IMPORT_LINE.findall(outside_code), [])


class ScriptOptionTests(unittest.TestCase):
    def test_every_option_written_after_a_script_is_one_it_takes(self):
        wrong = []
        for document in documents():
            for piece in code_pieces(text_of(document)):
                for script, rest in SCRIPT_COMMAND.findall(piece):
                    words = rest.split()
                    subcommand = (
                        words[0] if words and words[0] in subcommands(script) else None
                    )
                    written = LONG_OPTION.findall(rest)
                    if not written:
                        continue
                    known = script_options(script, None)
                    if isinstance(known, str):
                        wrong.append(f"{document}: {known}")
                        continue
                    if subcommand:
                        below = script_options(script, subcommand)
                        known = known | below if not isinstance(below, str) else known
                    for option in written:
                        if option not in known:
                            wrong.append(f"{document}: scripts/{script} {option}")
        self.assertEqual(sorted(set(wrong)), [])

    def test_the_shell_reading_finds_the_launchers_options(self):
        self.assertTrue({"--check-only", "--dev-identity"} <= script_options("run_pilot.sh", None))
        self.assertTrue({"--check-only", "--bootstrap-only"} <= script_options("run_dev.sh", None))


if __name__ == "__main__":
    unittest.main()
