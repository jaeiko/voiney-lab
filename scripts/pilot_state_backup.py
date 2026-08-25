#!/usr/bin/env python3
"""Create, verify, or restore one privacy-bounded controlled-pilot backup."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


BACKUP_SCHEMA_VERSION = 1
ALLOWED_OBJECT_SUFFIXES = {
    ".pdf",
    ".docx",
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".txt",
}
ALLOWED_OBJECT_ROOTS = {"objects", "evidence"}


class PilotBackupError(RuntimeError):
    pass


@dataclass(frozen=True)
class BackupSource:
    name: str
    path: Path
    kind: str


def _sha256(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _absolute_existing(path: str, *, directory: bool) -> Path:
    selected = Path(path)
    if not selected.is_absolute():
        raise PilotBackupError("Backup source paths must be absolute.")
    resolved = selected.resolve(strict=True)
    if resolved == Path(resolved.anchor):
        raise PilotBackupError("A filesystem root cannot be a backup source.")
    if directory and not resolved.is_dir():
        raise PilotBackupError("A configured backup data directory is not a directory.")
    if not directory and not resolved.is_file():
        raise PilotBackupError("A configured report database is not a file.")
    return resolved


def _sqlite_backup(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_uri = f"file:{source.as_posix()}?mode=ro"
    try:
        with sqlite3.connect(source_uri, uri=True) as original:
            result = original.execute("PRAGMA quick_check").fetchone()
            if result is None or result[0] != "ok":
                raise PilotBackupError("A source SQLite database failed quick_check.")
            with sqlite3.connect(destination) as copy:
                original.backup(copy)
                copied = copy.execute("PRAGMA quick_check").fetchone()
                if copied is None or copied[0] != "ok":
                    raise PilotBackupError("A copied SQLite database failed quick_check.")
    except sqlite3.Error as exc:
        raise PilotBackupError("A SQLite database could not be backed up.") from exc


def _copy_data_source(
    source: BackupSource,
    staging: Path,
    *,
    excluded_paths: tuple[Path, ...] = (),
) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    if source.kind == "report_database":
        destination = staging / source.name / source.path.name
        _sqlite_backup(source.path, destination)
        size, digest = _sha256(destination)
        return [{
            "path": destination.relative_to(staging).as_posix(),
            "kind": "sqlite",
            "byte_size": size,
            "sha256": digest,
        }]

    for item in sorted(source.path.rglob("*")):
        if item.is_symlink():
            raise PilotBackupError("Backup sources cannot contain symbolic links.")
        if any(item == excluded or excluded in item.parents for excluded in excluded_paths):
            continue
        if not item.is_file():
            continue
        relative = item.relative_to(source.path)
        if item.suffix.casefold() == ".sqlite":
            destination = staging / source.name / relative
            _sqlite_backup(item, destination)
            kind = "sqlite"
        elif (
            relative.parts
            and relative.parts[0] in ALLOWED_OBJECT_ROOTS
            and item.suffix.casefold() in ALLOWED_OBJECT_SUFFIXES
        ):
            destination = staging / source.name / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(item, destination)
            kind = "object"
        else:
            continue
        size, digest = _sha256(destination)
        entries.append({
            "path": destination.relative_to(staging).as_posix(),
            "kind": kind,
            "byte_size": size,
            "sha256": digest,
        })
    return entries


def _safe_archive_member(member: tarfile.TarInfo) -> bool:
    path = PurePosixPath(member.name)
    return bool(
        member.name
        and not path.is_absolute()
        and ".." not in path.parts
        and (member.isfile() or member.isdir())
        and not member.issym()
        and not member.islnk()
    )


def _extract_checked(archive: Path, destination: Path) -> None:
    try:
        with tarfile.open(archive, "r:gz") as bundle:
            members = bundle.getmembers()
            if not members or any(not _safe_archive_member(item) for item in members):
                raise PilotBackupError("Backup archive contains an unsafe member.")
            for member in members:
                target = (destination / member.name).resolve()
                try:
                    target.relative_to(destination.resolve())
                except ValueError as exc:
                    raise PilotBackupError(
                        "Backup archive member escaped the restore directory."
                    ) from exc
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                stream = bundle.extractfile(member)
                if stream is None:
                    raise PilotBackupError("Backup archive member could not be read.")
                with stream, target.open("xb") as output:
                    shutil.copyfileobj(stream, output, length=1024 * 1024)
    except (tarfile.TarError, OSError) as exc:
        raise PilotBackupError("Backup archive could not be extracted.") from exc


def _verify_tree(root: Path) -> dict[str, object]:
    manifest_path = root / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PilotBackupError("Backup manifest is missing or invalid.") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != BACKUP_SCHEMA_VERSION
        or not isinstance(manifest.get("files"), list)
    ):
        raise PilotBackupError("Backup manifest schema is unsupported.")
    declared: set[str] = set()
    for entry in manifest["files"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise PilotBackupError("Backup manifest file entry is invalid.")
        relative = PurePosixPath(entry["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise PilotBackupError("Backup manifest path is unsafe.")
        path = root.joinpath(*relative.parts)
        if not path.is_file() or path.is_symlink():
            raise PilotBackupError("A declared backup file is missing.")
        size, digest = _sha256(path)
        if size != entry.get("byte_size") or digest != entry.get("sha256"):
            raise PilotBackupError("A backup file failed its checksum.")
        if entry.get("kind") == "sqlite":
            try:
                with sqlite3.connect(
                    f"file:{path.as_posix()}?mode=ro&immutable=1",
                    uri=True,
                ) as db:
                    checked = db.execute("PRAGMA quick_check").fetchone()
            except sqlite3.Error as exc:
                raise PilotBackupError("A backed-up SQLite database is invalid.") from exc
            if checked is None or checked[0] != "ok":
                raise PilotBackupError("A backed-up SQLite database failed quick_check.")
        declared.add(relative.as_posix())
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path != manifest_path
    }
    if actual != declared:
        raise PilotBackupError("Backup archive contains undeclared files.")
    return manifest


def create_backup(args: argparse.Namespace) -> dict[str, object]:
    output = Path(args.output)
    if not output.is_absolute() or output.suffixes[-2:] != [".tar", ".gz"]:
        raise PilotBackupError("Backup output must be an absolute .tar.gz path.")
    if output.exists():
        raise PilotBackupError("Backup output already exists; refusing to overwrite it.")
    output.parent.mkdir(parents=True, exist_ok=True)
    sources = [
        BackupSource("protocol", _absolute_existing(args.protocol_data_dir, directory=True), "data_directory"),
        BackupSource("workspace", _absolute_existing(args.workspace_data_dir, directory=True), "data_directory"),
        BackupSource("reports", _absolute_existing(args.report_database, directory=False), "report_database"),
    ]
    with tempfile.TemporaryDirectory(prefix="pilot-backup-", dir=output.parent) as raw:
        staging = Path(raw) / "payload"
        staging.mkdir()
        entries: list[dict[str, object]] = []
        for source in sources:
            excluded_paths = tuple(
                candidate.path
                for candidate in sources
                if candidate is not source
                and candidate.path != source.path
                and candidate.path.is_relative_to(source.path)
            )
            entries.extend(
                _copy_data_source(
                    source,
                    staging,
                    excluded_paths=excluded_paths,
                )
            )
        if not any(item["kind"] == "sqlite" for item in entries):
            raise PilotBackupError("No SQLite database was found in the backup sources.")
        manifest = {
            "schema_version": BACKUP_SCHEMA_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "components": [source.name for source in sources],
            "files": sorted(entries, key=lambda item: str(item["path"])),
            "exclusions": {
                "environment_files": True,
                "raw_audio": True,
                "unallowlisted_files": True,
            },
        }
        manifest_path = staging / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        try:
            with tarfile.open(output, "x:gz") as bundle:
                bundle.add(manifest_path, arcname="manifest.json", recursive=False)
                for entry in manifest["files"]:
                    relative = PurePosixPath(str(entry["path"]))
                    item = staging.joinpath(*relative.parts)
                    bundle.add(item, arcname=relative.as_posix(), recursive=False)
        except (tarfile.TarError, OSError) as exc:
            output.unlink(missing_ok=True)
            raise PilotBackupError("Backup archive could not be created.") from exc
    verify_backup(output)
    return manifest


def verify_backup(archive: Path) -> dict[str, object]:
    archive = archive.resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="pilot-backup-verify-") as raw:
        root = Path(raw)
        _extract_checked(archive, root)
        return _verify_tree(root)


def restore_backup(archive: Path, destination: Path) -> dict[str, object]:
    archive = archive.resolve(strict=True)
    if not destination.is_absolute() or destination.exists():
        raise PilotBackupError(
            "Restore destination must be an absolute path that does not yet exist."
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="pilot-backup-restore-", dir=destination.parent
    ) as raw:
        staging = Path(raw) / "payload"
        staging.mkdir()
        _extract_checked(archive, staging)
        manifest = _verify_tree(staging)
        staging.rename(destination)
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--protocol-data-dir", required=True)
    create.add_argument("--workspace-data-dir", required=True)
    create.add_argument("--report-database", required=True)
    create.add_argument("--output", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("archive")
    restore = commands.add_parser("restore")
    restore.add_argument("archive")
    restore.add_argument("destination")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "create":
            manifest = create_backup(args)
        elif args.command == "verify":
            manifest = verify_backup(Path(args.archive))
        else:
            manifest = restore_backup(Path(args.archive), Path(args.destination))
    except (PilotBackupError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}")
        return 1
    print(
        f"OK: schema={manifest['schema_version']} files={len(manifest['files'])} "
        f"command={args.command}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
