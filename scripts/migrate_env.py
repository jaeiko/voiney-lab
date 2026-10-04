#!/usr/bin/env python3
"""Rename old setting names in a ``.env`` and rewrite it grouped by area.

    python scripts/migrate_env.py --check [PATH]
    python scripts/migrate_env.py --write [PATH]

PATH defaults to the repository's ``.env``.

``--check`` writes nothing. It lists names only -- the names that will be
renamed (old -> new), the names that stay, the names the code does not read,
and duplicates -- and exits 0 when the file needs no renaming, 1 when it does
or when a name is duplicated. Both modes exit 2 when they stop without
writing (no file, a line that is not ``.env`` syntax, a duplicate).

``--write`` first copies the file to ``<name>.bak-YYYYMMDD-HHMMSS`` beside it
(mode 600), then renames the old names and writes the settings back grouped by
area (공급자 키, OCR, 모델, 기능 켜기·끄기, 경로·저장소, 그 밖), sorted by name,
each under a short comment (mode 600). Names the code does not read are not
dropped: they go to the bottom, under "코드가 읽지 않는 설정". A comment block
written directly above a setting moves with it; a comment standing on its own
goes to the very end. It stops before writing anything when a name appears
twice -- including an old and a new name for the same setting, or two old
names that became one.

Values are never printed and never changed: the text after each ``=`` is
copied character for character. Lines the tool writes start with ``## `` and
are written anew each time, so running it again on its own output changes
nothing.

The names come from ``voiney_lab.setting_names`` (the table) and
``voiney_lab.setting_renames`` (old -> new).
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from dotenv.parser import parse_stream

from voiney_lab.setting_names import AREAS, BY_NAME, EXTERNAL_NAMES, KEYS
from voiney_lab.setting_renames import NEW_PREFIX, renamed

ROOT = Path(__file__).resolve().parents[1]
GENERATED = "## "
UNREAD_TITLE = "코드가 읽지 않는 설정"
LOOSE_COMMENTS_TITLE = "옛 파일에서 옮긴 주석 (바로 아래에 설정이 없던 것)"
HEADER = (
    "## VoineyLab 설정. scripts/migrate_env.py 가 영역별로 정리했다.\n"
    "## '## ' 로 시작하는 줄은 도구가 쓰는 주석이라, 다시 정리할 때 새로 쓴다.\n"
    "## 이름 표: src/voiney_lab/setting_names.py\n"
)
SECRET_REFERENCES = "VOINEY_LAB_SECRET_REFERENCES"


class MigrationError(Exception):
    """The file cannot be read or migrated as it stands; nothing was written."""


@dataclass
class Entry:
    name: str
    new_name: str
    text: str
    line: int
    comments: list[str] = field(default_factory=list)


@dataclass
class Parsed:
    entries: list[Entry]
    loose_comments: list[str]
    secret_reference_names: frozenset[str]


def _leading_blank(text: str) -> tuple[int, str]:
    """Split off the blank lines the parser glues in front of a binding."""

    lines = text.splitlines(keepends=True)
    skipped = 0
    while skipped < len(lines) and not lines[skipped].strip():
        skipped += 1
    return skipped, "".join(lines[skipped:])


def parse(text: str) -> Parsed:
    entries: list[Entry] = []
    loose: list[str] = []
    pending: list[str] = []
    secret_value: str | None = None
    for binding in parse_stream(io.StringIO(text)):
        blank_lines, body = _leading_blank(binding.original.string)
        line_number = binding.original.line + blank_lines
        if binding.error:
            raise MigrationError(f"{line_number}번째 줄을 .env 로 읽을 수 없습니다.")
        if blank_lines and pending:
            loose.extend(pending)
            pending = []
        if binding.key is None:
            line = body if body.endswith("\n") else body + "\n"
            if not line.strip():
                continue
            if not line.startswith(GENERATED):
                pending.append(line)
            continue
        new_name = renamed(binding.key) or binding.key
        if new_name == SECRET_REFERENCES:
            secret_value = binding.value
        entries.append(Entry(
            binding.key, new_name, body if body.endswith("\n") else body + "\n",
            line_number, pending,
        ))
        pending = []
    loose.extend(pending)
    return Parsed(entries, loose, _secret_reference_names(secret_value))


def _secret_reference_names(value: str | None) -> frozenset[str]:
    """The variable names VOINEY_LAB_SECRET_REFERENCES points at, if it parses."""

    try:
        mapping = json.loads(value) if value else {}
    except json.JSONDecodeError:
        return frozenset()
    if not isinstance(mapping, dict):
        return frozenset()
    return frozenset(item for item in mapping.values() if isinstance(item, str))


def area_of(name: str, secret_reference_names: frozenset[str]) -> str | None:
    """The area a name is written under, or None when the code does not read it."""

    if name in BY_NAME:
        return BY_NAME[name].area
    if name in secret_reference_names:
        return KEYS
    area, _ = EXTERNAL_NAMES.get(name, (None, ""))
    return area


def meaning_of(name: str, secret_reference_names: frozenset[str]) -> str | None:
    """The short comment written above a setting, if there is one to write."""

    if name in BY_NAME:
        return BY_NAME[name].meaning
    if name in secret_reference_names:
        return f"{SECRET_REFERENCES} 가 가리키는 비밀"
    if name in EXTERNAL_NAMES:
        return EXTERNAL_NAMES[name][1]
    if name.startswith(NEW_PREFIX):
        return "표에 없는 이름: 오타이거나 더는 쓰지 않는 설정"
    return None


def duplicates(entries: Sequence[Entry]) -> dict[str, list[Entry]]:
    grouped: dict[str, list[Entry]] = {}
    for entry in entries:
        grouped.setdefault(entry.new_name, []).append(entry)
    return {name: group for name, group in grouped.items() if len(group) > 1}


def old_secret_references(parsed: Parsed) -> list[str]:
    """Referenced names the rename would move: the reference would break."""

    return sorted(name for name in parsed.secret_reference_names if renamed(name))


def renamed_text(entry: Entry) -> str:
    if entry.name == entry.new_name:
        return entry.text
    pattern = re.compile(r"^(\s*(?:export\s+)?)" + re.escape(entry.name) + r"(?=\s*(?:=|$))")
    result, count = pattern.subn(lambda match: match.group(1) + entry.new_name, entry.text, 1)
    if count != 1:
        raise MigrationError(
            f"{entry.line}번째 줄의 이름을 바꿀 수 없습니다 (따옴표로 감싼 이름 등)."
        )
    return result


def render(parsed: Parsed) -> str:
    areas: dict[str | None, list[Entry]] = {}
    for entry in parsed.entries:
        areas.setdefault(
            area_of(entry.new_name, parsed.secret_reference_names), []
        ).append(entry)
    out = [HEADER]
    for title, key in [(area, area) for area in AREAS] + [(UNREAD_TITLE, None)]:
        group = sorted(areas.get(key, []), key=lambda entry: entry.new_name)
        if not group:
            continue
        out.append(f"\n{GENERATED}── {title} ──\n")
        for entry in group:
            out.extend(entry.comments)
            meaning = meaning_of(entry.new_name, parsed.secret_reference_names)
            if meaning:
                out.append(f"{GENERATED}{meaning}\n")
            out.append(renamed_text(entry))
    if parsed.loose_comments:
        out.append(f"\n{GENERATED}── {LOOSE_COMMENTS_TITLE} ──\n")
        out.extend(parsed.loose_comments)
    return "".join(out)


def report(parsed: Parsed, stream) -> int:
    """Print names only. 0 when nothing needs renaming and nothing repeats."""

    entries = parsed.entries
    moving = [entry for entry in entries if entry.name != entry.new_name]
    staying = [
        entry for entry in entries
        if entry.name == entry.new_name
        and area_of(entry.new_name, parsed.secret_reference_names) is not None
    ]
    unread = [
        entry for entry in entries
        if area_of(entry.new_name, parsed.secret_reference_names) is None
    ]
    repeated = duplicates(entries)
    broken_references = old_secret_references(parsed)

    def section(title: str, lines: list[str]) -> None:
        print(f"{title} ({len(lines)}개)", file=stream)
        for line in lines:
            print(f"  {line}", file=stream)

    section("바뀔 이름 (옛 → 새)", [
        f"{entry.name} → {entry.new_name}" for entry in moving
    ])
    section("그대로인 이름", [entry.name for entry in staying])
    section("코드가 읽지 않는 이름", [
        entry.new_name + (f" (옛 이름 {entry.name})" if entry.name != entry.new_name else "")
        for entry in unread
    ])
    section("중복", [
        f"{name}: " + ", ".join(f"{entry.name} ({entry.line}번째 줄)" for entry in group)
        for name, group in sorted(repeated.items())
    ])
    if broken_references:
        section(f"{SECRET_REFERENCES} 가 가리키는 옛 이름", broken_references)
    return 1 if moving or repeated or broken_references else 0


def _write_private(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if descriptor != -1:
            os.close(descriptor)


def write(path: Path, parsed: Parsed, original: bytes, now: datetime) -> Path:
    repeated = duplicates(parsed.entries)
    if repeated:
        raise MigrationError(
            "중복 이름이 있어 쓰지 않았습니다: " + ", ".join(sorted(repeated))
            + ". 하나만 남기고 다시 실행하세요."
        )
    broken = old_secret_references(parsed)
    if broken:
        raise MigrationError(
            f"{SECRET_REFERENCES} 가 옛 이름 {len(broken)}개를 가리킵니다: "
            + ", ".join(broken)
            + ". 값은 바꾸지 않으므로 그 값을 직접 고친 뒤 다시 실행하세요."
        )
    rendered = render(parsed).encode("utf-8")
    backup = path.with_name(f"{path.name}.bak-{now:%Y%m%d-%H%M%S}")
    if backup.exists():
        raise MigrationError(f"백업 파일이 이미 있습니다: {backup.name}")
    _write_private(backup, original)
    temporary = path.with_name(f".{path.name}.migrate-{os.getpid()}")
    try:
        _write_private(temporary, rendered)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return backup


def main(argv: Sequence[str] | None = None, *, now: datetime | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rename old setting names in a .env (names only are printed)."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="이름만 보이고 쓰지 않는다")
    mode.add_argument("--write", action="store_true", help="백업한 뒤 이름을 바꾸고 다시 쓴다")
    parser.add_argument("path", nargs="?", type=Path, default=ROOT / ".env")
    arguments = parser.parse_args(argv)
    path: Path = arguments.path
    try:
        if path.is_symlink():
            raise MigrationError(f"심볼릭 링크는 다루지 않습니다: {path}")
        if not path.is_file():
            raise MigrationError(f"파일이 없습니다: {path}")
        original = path.read_bytes()
        try:
            text = original.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MigrationError("UTF-8 로 읽을 수 없습니다.") from exc
        parsed = parse(text)
        if arguments.check:
            return report(parsed, sys.stdout)
        report(parsed, sys.stdout)
        backup = write(path, parsed, original, now or datetime.now())
    except MigrationError as exc:
        print(f"[멈춤] {exc}", file=sys.stderr)
        return 2
    print(f"백업: {backup}")
    print(f"다시 씀: {path} (권한 600)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
