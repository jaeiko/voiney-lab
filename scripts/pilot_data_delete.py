#!/usr/bin/env python3
"""Preview, confirm and delete the controlled pilot's collected data (lane PL).

The pilot notice promises that collected data is used only to improve the
service and is deleted when the pilot ends. This tool keeps that promise in
three steps, because a deletion cannot be undone:

1. **Preview** (the default): list what is in the pilot data folder and how
   many of each -- uploaded PDFs, analysis and translation records, experiment
   records, voice diagnostics -- and print the confirmation word for exactly
   that content. Nothing is deleted.
2. **Confirm**: ``--delete --confirm WORD``. The word is derived from what the
   preview listed, so it no longer matches if the folder changed since (the
   server was still writing, a file was added): preview again.
3. **Record**: what was deleted and when is appended to
   ``deletion_log.jsonl`` in the same folder. The log holds category names,
   counts and byte sizes only -- no file names from the data, no content, no
   user identifier -- and the tool never deletes it.

It works only on the pilot data folder (``data/runtime/pilot``, the one
``scripts/run_pilot.sh`` writes) or a folder that carries the pilot marker file
that launcher creates. Every development data folder is refused, and so is a
folder that holds a symbolic link. It also refuses while another process holds
one of the files open (stop the pilot server first).

It never touches ``approved_safety_catalog.sqlite``: that file is the
institution's reviewed safety documents, not data collected from participants.
Backups made with ``scripts/pilot_state_backup.py`` are copies this tool does
not know about; delete them too.

**One account** (lane ON, decision ①, 2026-10-18): ``--account NAME`` previews
and deletes what one account left -- its experiment sessions with their
events, completed steps, observations and evidence (rows and files), the
experiment records bound to it, its pilot metrics rows (turns, marks, protocol
events, ratings; lane FX5), its settings, access records and resource
ownership, and the account itself with its sessions and login attempts --
through the same preview -> confirm -> delete -> record steps, inside the
stores (the append-only triggers are lifted for the deletion and put back).
The protocols the account uploaded are the lab's shared list and stay; the
identity row stays, without its name, where such a protocol still names it
as its owner. The log holds categories and counts only, never the name.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PILOT_DATA_DIR = ROOT / "data" / "runtime" / "pilot"
#: Written by scripts/run_pilot.sh; a folder without it (and not the pilot
#: folder above) is not pilot data as far as this tool can tell.
PILOT_MARKER = ".voiney-pilot-data"
LOG_NAME = "deletion_log.jsonl"
#: Development data lives here or beside it; never delete in these.
DEVELOPMENT_FOLDERS = (
    ROOT / "data" / "runtime" / "candidate-a-live-acceptance",
    ROOT / "data" / "runtime" / "ci-e2e",
    ROOT / "data" / "runtime" / "candidate-a-source",
    ROOT / "data" / "runtime" / "stt-diagnostics",
    ROOT / "data" / "development_cache",
    ROOT / "data" / "fixtures",
)
SQLITE_SIDE_FILES = ("", "-wal", "-shm", "-journal")

#: (category, what it holds, entries inside the data folder). An entry ending
#: in "/" is a folder; otherwise it is a SQLite file with its side files.
CATEGORIES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("uploaded_originals", "업로드한 프로토콜 원문 PDF", ("objects/",)),
    ("analysis_records", "분석·OCR·확인 질문 기록 (프로토콜 카탈로그)", ("protocol_workspace.sqlite",)),
    ("experiment_records", "실험 기록 (실험 장부, 보고서 문장)", ("experiment_reports.sqlite",)),
    ("workspace_records", "실험 세션·관찰·번역 문장·증거 파일·설정 (작업공간)", ("workspace/",)),
    ("voice_records", "음성 진단 녹음과 전사 (켰을 때만 있음)", ("stt-diagnostics/",)),
)
#: Left alone on purpose, named in the preview so nobody wonders.
KEPT = {
    "approved_safety_catalog.sqlite": "기관 안전 문서 (참가자 데이터가 아님)",
    LOG_NAME: "삭제 기록",
    PILOT_MARKER: "파일럿 폴더 표시",
}
TABLE_LABELS = {
    "pdf_objects": "업로드 PDF",
    "protocol_revisions": "프로토콜 리비전",
    "analysis_revisions": "분석 결과",
    "clarifications": "확인 질문",
    "protocol_events": "카탈로그 사건",
    "experiments": "실험(프로토콜 묶음)",
    "experiment_reports": "실험 기록",
    "experiment_report_events": "실험 기록 사건",
    "experiment_report_prose": "보고서 문장",
    "experiment_sessions": "실험 세션",
    "experiment_session_events": "실험 세션 사건",
    "experiment_observations": "관찰 기록",
    "experiment_evidence": "증거 파일 기록",
    "protocol_fact_translations": "번역 문장",
    "protocol_translations": "번역",
    "protocol_translation_glossaries": "번역 용어집",
    "analytics_events": "분석 지표 사건",
    "experimenter_settings": "실험자 설정",
    "evidence_files": "증거 파일",
    "files_pdf": "PDF",
    "files_wav": "녹음(wav)",
    "files_json": "전사 기록(json)",
    "files_photo": "사진",
}
#: How a file in a folder is counted in the preview: by its kind.
FILE_KINDS = {
    ".pdf": "files_pdf", ".wav": "files_wav", ".json": "files_json",
    ".jpg": "files_photo", ".jpeg": "files_photo", ".png": "files_photo", ".webp": "files_photo",
}


class RefusedError(RuntimeError):
    """The request is refused before anything is deleted (exit status 2)."""


# --- One account's data (lane ON, decision ①; one scope with the server's, lane FX5) -
# What an account leaves, and how it is deleted, is written once in
# ``voiney_lab.accounts`` (``account_data_inventory``, ``delete_account_data``):
# the account deleting itself in the app deletes the same things.
ACCOUNT_TABLE_LABELS = {
    **TABLE_LABELS,
    "admin_audit_events": "접근 기록",
    "resource_bindings": "자원 소유 기록",
    "memberships": "구성원 기록",
    "principals": "신원 행",
    "accounts": "계정",
    "sessions": "로그인 세션",
    "login_attempts": "로그인 시도",
    "pilot_turns": "지표 턴",
    "pilot_marks": "지표 표시",
    "pilot_protocol_events": "지표 프로토콜 사건",
    "pilot_feedback": "지표 평점",
}


def account_inventory(data_dir: Path, name: str, *, in_place: bool = False):
    """The account and what it left: (account facts, entries, notes), as ``accounts.account_data_inventory``."""

    from voiney_lab import accounts as acc

    try:
        return acc.account_data_inventory(acc.AccountDataLocations.pilot_folder(data_dir), name, in_place=in_place)
    except acc.AccountNotFoundError as exc:
        raise RefusedError(f"그런 계정이 없습니다: {acc.normalize_name(name)}") from exc
    except acc.AccountError as exc:
        raise RefusedError(f"계정 저장소가 없습니다: {data_dir / 'workspace' / acc.ACCOUNTS_DATABASE_FILENAME}") from exc


def delete_account_data(data_dir: Path, facts: dict, entries) -> list[dict[str, object]]:
    """Delete the rows and files of the preview; one record per store."""

    from voiney_lab import accounts as acc

    return acc.delete_account_data(acc.AccountDataLocations.pilot_folder(data_dir), facts, entries)


def account_confirmation_word(facts: dict, entries: list) -> str:
    """Six hex digits that name exactly the rows and files the preview listed."""

    digest = hashlib.sha256()
    digest.update(str(facts["account_id"]).encode("utf-8"))
    for entry in entries:
        for key in sorted(entry.keys):
            digest.update(f"{entry.category}\0{key}\n".encode("utf-8"))
        for file in sorted(entry.files):
            digest.update(f"{file}\0{file.stat().st_size}\n".encode("utf-8"))
    return digest.hexdigest()[:6].upper()


def render_account_preview(data_dir: Path, facts: dict, entries: list, notes: list[str]) -> str:
    lines = [
        "=== 파일럿 데이터 삭제 — 계정 하나 — 미리보기 (아무것도 지우지 않았습니다) ===",
        f"데이터 폴더: {data_dir}",
        f"계정: {facts['name']} (연구실 {facts['organization_id']})",
        "",
    ]
    for number, entry in enumerate(entries, 1):
        lines.append(f"[{number}] {entry.label}")
        counted = [f"{ACCOUNT_TABLE_LABELS.get(table, table)} {count}" for table, count in entry.rows.items() if count]
        if entry.files:
            counted.append(f"파일 {len(entry.files)}개, {_megabytes(sum(file.stat().st_size for file in entry.files))}")
        lines.append("    안에 든 것: " + (", ".join(counted) if counted else "기록 없음"))
    for note in notes:
        lines.append("지우지 않는 것: " + note)
    lines.append("")
    return "\n".join(lines)


def run_account(arguments: argparse.Namespace, out) -> int:
    """``--account NAME``: preview, then delete one account's data on confirmation."""

    data_dir = check_data_dir(arguments.data_dir)
    facts, entries, notes = account_inventory(data_dir, arguments.account)
    word = account_confirmation_word(facts, entries)
    print(render_account_preview(data_dir, facts, entries, notes), file=out)
    if not arguments.delete:
        print("지우려면 파일럿 서버를 멈춘 뒤 이 줄을 그대로 실행하세요:", file=out)
        print(f"  python scripts/pilot_data_delete.py --account {shlex_quote(facts['name'])} --delete --confirm {word}", file=out)
        print("(되돌릴 수 없습니다. 이 사람과 약속한 보고서는 먼저 내려받으세요.)", file=out)
        return 0
    if arguments.confirm is None:
        raise RefusedError("--delete 에는 미리보기가 알려 준 --confirm 확인 단어가 필요합니다.")
    if arguments.confirm.strip().upper() != word:
        raise RefusedError("확인 단어가 맞지 않습니다. 미리보기 뒤 내용이 바뀌었을 수 있으니 처음부터 다시 하세요.")
    open_by = holders(data_dir)
    if open_by:
        raise RefusedError(
            "다른 프로세스(pid " + ", ".join(map(str, open_by)) + ")가 이 폴더의 파일을 열고 있습니다. "
            "파일럿 서버를 멈춘 뒤 다시 하세요.")
    try:
        probe = os.open(data_dir / LOG_NAME, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.close(probe)
    except OSError as exc:
        raise RefusedError("삭제 기록을 쓸 수 없어 아무것도 지우지 않았습니다.") from exc
    # Read again in place: the deletion names the rows as they are now.
    facts, entries, _ = account_inventory(data_dir, arguments.account, in_place=True)
    if account_confirmation_word(facts, entries) != word:
        raise RefusedError("미리보기 뒤에 내용이 바뀌었습니다. 처음부터 다시 하세요.")
    results = delete_account_data(data_dir, facts, entries)
    write_log(data_dir, word, results, scope="account")
    failed = [item for item in results if item["result"] != "deleted"]
    for item in results:
        print(f"{item['result']}: {item['entry']}", file=out)
    print(f"삭제 기록: {data_dir / LOG_NAME}", file=out)
    return 1 if failed else 0


def shlex_quote(value: str) -> str:
    import shlex

    return shlex.quote(value)


@dataclass
class Entry:
    category: str
    label: str
    relative: str
    path: Path
    is_tree: bool
    files: int = 0
    size: int = 0
    rows: dict[str, int] = field(default_factory=dict)
    unreadable: bool = False


def _is_inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def check_data_dir(data_dir: Path) -> Path:
    """The folder this tool may work on, or a refusal saying why not."""

    raw = Path(data_dir)
    if not raw.is_absolute():
        raw = Path.cwd() / raw
    if raw.is_symlink():
        raise RefusedError("데이터 폴더가 심볼릭 링크입니다.")
    try:
        resolved = raw.resolve(strict=True)
    except FileNotFoundError as exc:
        if raw.resolve() == PILOT_DATA_DIR.resolve():
            # The pilot has not been started here: nothing was collected.
            return raw.resolve()
        raise RefusedError(f"데이터 폴더가 없습니다: {raw}") from exc
    if not resolved.is_dir():
        raise RefusedError(f"데이터 폴더가 폴더가 아닙니다: {resolved}")
    for development in DEVELOPMENT_FOLDERS:
        if resolved == development.resolve() or _is_inside(resolved, development.resolve()):
            raise RefusedError(
                f"개발 데이터 폴더에서는 쓸 수 없습니다: {resolved}. "
                "이 도구는 파일럿 데이터 폴더만 지웁니다.")
    if resolved == PILOT_DATA_DIR.resolve():
        return resolved
    if (resolved / PILOT_MARKER).is_file():
        return resolved
    raise RefusedError(
        f"파일럿 데이터 폴더가 아닙니다: {resolved}. 파일럿 실행기(scripts/run_pilot.sh)가 "
        f"쓰는 data/runtime/pilot 이거나, 그 실행기가 만든 {PILOT_MARKER} 파일이 있는 폴더여야 합니다.")


def _walk_files(tree: Path) -> list[Path]:
    found: list[Path] = []
    for current, directories, names in os.walk(tree):
        for name in directories + names:
            if (Path(current) / name).is_symlink():
                raise RefusedError("데이터 폴더 안에 심볼릭 링크가 있어 지우지 않습니다.")
        found.extend(Path(current) / name for name in names)
    return found


def _sqlite_rows(path: Path) -> dict[str, int]:
    """Row counts per table, read from a copy so the pilot folder is not touched.

    Opening a WAL-mode database in place -- even read-only -- creates its
    ``-shm`` file, and the preview must change nothing. The copy (with the
    write-ahead log beside it, so a crashed server's last writes are counted)
    lives in a temporary folder that is removed at once.
    """

    with tempfile.TemporaryDirectory(prefix="pilot-preview-") as temp:
        copy = Path(temp) / "copy.sqlite"
        shutil.copyfile(path, copy)
        wal = Path(str(path) + "-wal")
        if wal.is_file():
            shutil.copyfile(wal, Path(str(copy) + "-wal"))
        connection = sqlite3.connect(copy)
        try:
            names = [
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                    "AND name NOT LIKE '%metadata%' ORDER BY name")
            ]
            counts: dict[str, int] = {}
            for name in names:
                quoted = '"' + name.replace('"', '""') + '"'
                counts[name] = connection.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0]
            return counts
        finally:
            connection.close()


def inventory(data_dir: Path) -> tuple[list[Entry], list[str]]:
    """What a deletion would remove, and what is in the folder besides."""

    entries: list[Entry] = []
    known: set[str] = set(KEPT)
    if not data_dir.exists():
        return entries, []
    for category, label, relatives in CATEGORIES:
        for relative in relatives:
            if relative.endswith("/"):
                name = relative.rstrip("/")
                known.add(name)
                path = data_dir / name
                if not path.exists():
                    continue
                if not path.is_dir() or path.is_symlink():
                    raise RefusedError(f"{name} 이(가) 폴더가 아닙니다.")
                files = _walk_files(path)
                entry = Entry(category, label, relative, path, True, len(files),
                              sum(item.stat().st_size for item in files))
                for item in files:
                    kind = FILE_KINDS.get(item.suffix.casefold())
                    if kind is not None:
                        entry.rows[kind] = entry.rows.get(kind, 0) + 1
                for database in sorted(path.rglob("*.sqlite")):
                    try:
                        for table, count in _sqlite_rows(database).items():
                            entry.rows[table] = entry.rows.get(table, 0) + count
                    except sqlite3.Error:
                        entry.unreadable = True
                evidence = path / "evidence"
                if evidence.is_dir():
                    entry.rows["evidence_files"] = len(_walk_files(evidence))
                entries.append(entry)
            else:
                for suffix in SQLITE_SIDE_FILES:
                    known.add(relative + suffix)
                path = data_dir / relative
                present = [data_dir / (relative + suffix) for suffix in SQLITE_SIDE_FILES
                           if (data_dir / (relative + suffix)).exists()]
                if not present:
                    continue
                if any(item.is_symlink() or not item.is_file() for item in present):
                    raise RefusedError(f"{relative} 이(가) 보통 파일이 아닙니다.")
                entry = Entry(category, label, relative, path, False, len(present),
                              sum(item.stat().st_size for item in present))
                try:
                    entry.rows = _sqlite_rows(path)
                except sqlite3.Error:
                    entry.unreadable = True
                entries.append(entry)
    others = sorted(item.name for item in data_dir.iterdir() if item.name not in known)
    return entries, others


def _files_of(entry: Entry) -> list[Path]:
    if entry.is_tree:
        return _walk_files(entry.path)
    return [Path(str(entry.path) + suffix) for suffix in SQLITE_SIDE_FILES
            if Path(str(entry.path) + suffix).exists()]


def confirmation_word(data_dir: Path, entries: list[Entry]) -> str:
    """Six hex digits that name exactly the files the preview listed."""

    digest = hashlib.sha256()
    for entry in entries:
        for item in sorted(_files_of(entry)):
            digest.update(item.relative_to(data_dir).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(str(item.stat().st_size).encode("ascii"))
            digest.update(b"\n")
    return digest.hexdigest()[:6].upper()


def _megabytes(size: int) -> str:
    return f"{size / 1_000_000:.1f} MB" if size >= 100_000 else f"{size} B"


def render_preview(data_dir: Path, entries: list[Entry], others: list[str]) -> str:
    lines = [
        "=== 파일럿 데이터 삭제 — 미리보기 (아무것도 지우지 않았습니다) ===",
        f"데이터 폴더: {data_dir}",
        "",
    ]
    if not entries:
        lines.append("지울 데이터가 없습니다.")
    for number, entry in enumerate(entries, 1):
        what = f"폴더 {entry.relative}" if entry.is_tree else f"{entry.relative}"
        lines.append(f"[{number}] {entry.label}")
        lines.append(f"    {what} — 파일 {entry.files}개, {_megabytes(entry.size)}")
        counted = [f"{TABLE_LABELS.get(table, table)} {count}" for table, count in entry.rows.items() if count]
        if counted:
            lines.append("    안에 든 것: " + ", ".join(counted))
        elif not entry.is_tree and not entry.unreadable:
            lines.append("    안에 든 것: 기록 없음 (빈 저장소)")
        if entry.unreadable:
            lines.append("    (SQLite 를 읽지 못했습니다. 개수를 셀 수 없지만 파일은 지워집니다)")
    lines.append("")
    kept = [f"{name} — {reason}" for name, reason in KEPT.items() if (data_dir / name).exists()]
    if kept:
        lines.append("지우지 않는 것: " + "; ".join(kept))
    if others:
        lines.append("이 도구가 모르는 파일 (지우지 않습니다): " + ", ".join(others))
    lines.append("")
    return "\n".join(lines)


def holders(data_dir: Path) -> list[int]:
    """Process ids (not names) that hold a file under ``data_dir`` open; Linux only."""

    proc = Path("/proc")
    if not proc.is_dir():
        return []
    found: set[int] = set()
    prefix = str(data_dir) + os.sep
    for process in proc.iterdir():
        if not process.name.isdigit() or int(process.name) == os.getpid():
            continue
        try:
            for descriptor in (process / "fd").iterdir():
                try:
                    target = os.readlink(descriptor)
                except OSError:
                    continue
                if target.startswith(prefix):
                    found.add(int(process.name))
                    break
        except OSError:
            continue
    return sorted(found)


def delete(data_dir: Path, entries: list[Entry]) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for entry in entries:
        record: dict[str, object] = {
            "category": entry.category, "entry": entry.relative,
            "files": entry.files, "bytes": entry.size,
            "rows": {table: count for table, count in entry.rows.items() if count},
        }
        try:
            if entry.is_tree:
                shutil.rmtree(entry.path)
            else:
                for item in _files_of(entry):
                    item.unlink()
            record["result"] = "deleted"
        except OSError as exc:
            record["result"] = "failed"
            record["error"] = type(exc).__name__
        results.append(record)
    return results


def write_log(data_dir: Path, word: str, results: list[dict[str, object]], *, scope: str = "folder") -> None:
    record = {
        "deleted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data_dir": str(data_dir),
        "scope": scope,
        "via": "tool",
        "confirmation": word,
        "result": "deleted" if all(item["result"] == "deleted" for item in results) else "partial",
        "categories": results,
    }
    descriptor = os.open(data_dir / LOG_NAME, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(descriptor, (json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def run(arguments: argparse.Namespace, out=None) -> int:
    out = out if out is not None else sys.stdout
    if getattr(arguments, "account", None):
        return run_account(arguments, out)
    data_dir = check_data_dir(arguments.data_dir)
    entries, others = inventory(data_dir)
    word = confirmation_word(data_dir, entries)
    print(render_preview(data_dir, entries, others), file=out)
    if not arguments.delete:
        if entries:
            print("지우려면 파일럿 서버를 멈춘 뒤 이 줄을 그대로 실행하세요:", file=out)
            print(f"  python scripts/pilot_data_delete.py --delete --confirm {word}", file=out)
            print("(되돌릴 수 없습니다. 보관할 보고서는 먼저 내려받고, 백업본도 따로 지우세요.)", file=out)
        return 0
    if not entries:
        return 0
    if arguments.confirm is None:
        raise RefusedError("--delete 에는 미리보기가 알려 준 --confirm 확인 단어가 필요합니다.")
    if arguments.confirm.strip().upper() != word:
        raise RefusedError("확인 단어가 맞지 않습니다. 미리보기 뒤에 폴더 내용이 바뀌었을 수 있으니 처음부터 다시 하세요.")
    open_by = holders(data_dir)
    if open_by:
        raise RefusedError(
            "다른 프로세스(pid " + ", ".join(map(str, open_by)) + ")가 이 폴더의 파일을 열고 있습니다. "
            "파일럿 서버를 멈춘 뒤 다시 하세요.")
    try:
        probe = os.open(data_dir / LOG_NAME, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.close(probe)
    except OSError as exc:
        raise RefusedError("삭제 기록을 쓸 수 없어 아무것도 지우지 않았습니다.") from exc
    results = delete(data_dir, entries)
    write_log(data_dir, word, results)
    failed = [item for item in results if item["result"] != "deleted"]
    for item in results:
        print(f"{item['result']}: {item['entry']} (파일 {item['files']}개)", file=out)
    print(f"삭제 기록: {data_dir / LOG_NAME}", file=out)
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="파일럿 데이터를 미리보고, 확인하고, 지운다.")
    parser.add_argument("--data-dir", type=Path, default=PILOT_DATA_DIR,
                        help="파일럿 데이터 폴더 (기본 data/runtime/pilot)")
    parser.add_argument("--delete", action="store_true", help="미리보기 뒤 확인 단어와 함께 실제로 지운다")
    parser.add_argument("--confirm", help="미리보기가 알려 준 확인 단어")
    parser.add_argument("--account", metavar="NAME",
                        help="이 계정이 남긴 데이터만 미리보고 지운다 (줄 ON 결정 ①)")
    arguments = parser.parse_args(argv)
    try:
        return run(arguments)
    except RefusedError as exc:
        print(f"[REFUSED] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
