from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import tarfile
from pathlib import Path

from scripts import pilot_state_backup


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "pilot_state_backup.py"


def _database(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE evidence(value TEXT NOT NULL)")
        connection.execute("INSERT INTO evidence VALUES(?)", (value,))
        connection.commit()


def _wal_database(path: Path, value: str) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    connection.execute("CREATE TABLE evidence(value TEXT NOT NULL)")
    connection.execute("INSERT INTO evidence VALUES(?)", (value,))
    connection.commit()
    connection.execute("SELECT value FROM evidence").fetchone()
    assert Path(f"{path}-wal").is_file()
    assert Path(f"{path}-shm").is_file()
    return connection


def _run(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *arguments],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )


def test_pilot_backup_create_verify_restore_is_consistent_and_allowlisted(tmp_path):
    protocol = tmp_path / "protocol"
    workspace = tmp_path / "workspace"
    reports = tmp_path / "reports" / "experiment_reports.sqlite"
    _database(protocol / "protocol_workspace.sqlite", "protocol")
    _database(workspace / "commercial_workspace.sqlite", "workspace")
    _database(reports, "reports")
    protocol_object = protocol / "objects" / "sha256" / "aa" / "a.pdf"
    protocol_object.parent.mkdir(parents=True)
    protocol_object.write_bytes(b"%PDF-1.4\ncontrolled pilot protocol\n")
    evidence = workspace / "evidence" / "tenant" / "session" / "evidence.jpg"
    evidence.parent.mkdir(parents=True)
    evidence.write_bytes(b"not-real-image-but-opaque-evidence")
    (workspace / ".env").write_text("SECRET=must-not-back-up\n", encoding="utf-8")
    diagnostic = workspace / "diagnostics" / "raw.wav"
    diagnostic.parent.mkdir()
    diagnostic.write_bytes(b"raw audio must not back up")
    archive = tmp_path / "pilot-backup.tar.gz"

    created = _run(
        "create",
        "--protocol-data-dir",
        str(protocol),
        "--workspace-data-dir",
        str(workspace),
        "--report-database",
        str(reports),
        "--output",
        str(archive),
    )
    assert created.returncode == 0, created.stdout + created.stderr
    assert "files=5" in created.stdout
    with tarfile.open(archive, "r:gz") as bundle:
        names = set(bundle.getnames())
    assert "protocol/protocol_workspace.sqlite" in names
    assert "workspace/commercial_workspace.sqlite" in names
    assert "reports/experiment_reports.sqlite" in names
    assert "protocol/objects/sha256/aa/a.pdf" in names
    assert "workspace/evidence/tenant/session/evidence.jpg" in names
    assert not any(".env" in name or name.endswith(".wav") for name in names)

    verified = _run("verify", str(archive))
    assert verified.returncode == 0, verified.stdout + verified.stderr
    restored = tmp_path / "restored"
    result = _run("restore", str(archive), str(restored))
    assert result.returncode == 0, result.stdout + result.stderr
    with sqlite3.connect(
        restored / "workspace" / "commercial_workspace.sqlite"
    ) as connection:
        assert connection.execute("SELECT value FROM evidence").fetchone()[0] == "workspace"
    assert (
        restored
        / "workspace"
        / "evidence"
        / "tenant"
        / "session"
        / "evidence.jpg"
    ).read_bytes() == evidence.read_bytes()

    collision = _run(
        "create",
        "--protocol-data-dir",
        str(protocol),
        "--workspace-data-dir",
        str(workspace),
        "--report-database",
        str(reports),
        "--output",
        str(archive),
    )
    assert collision.returncode == 1
    assert "refusing to overwrite" in collision.stdout


def test_pilot_backup_rejects_corrupt_archives_and_relative_sources(tmp_path):
    corrupt = tmp_path / "corrupt.tar.gz"
    corrupt.write_bytes(b"not a backup")
    verified = _run("verify", str(corrupt))
    assert verified.returncode == 1
    assert "could not be extracted" in verified.stdout

    rejected = _run(
        "create",
        "--protocol-data-dir",
        "relative/protocol",
        "--workspace-data-dir",
        str(tmp_path),
        "--report-database",
        str(corrupt),
        "--output",
        str(tmp_path / "rejected.tar.gz"),
    )
    assert rejected.returncode == 1
    assert "must be absolute" in rejected.stdout


def test_candidate_a_nested_wal_backup_archives_only_manifest_files(
    tmp_path,
    monkeypatch,
):
    runtime = tmp_path / "candidate-a-live-acceptance"
    workspace = runtime / "workspace"
    reports = runtime / "experiment_reports.sqlite"
    source_databases = (
        runtime / "protocol_workspace.sqlite",
        reports,
        workspace / "commercial_workspace.sqlite",
    )
    source_connections = [
        _wal_database(source_databases[0], "protocol"),
        _wal_database(source_databases[1], "reports"),
        _wal_database(source_databases[2], "workspace"),
    ]
    protocol_object = runtime / "objects" / "sha256" / "aa" / "source.pdf"
    protocol_object.parent.mkdir(parents=True)
    protocol_object.write_bytes(b"%PDF-1.4\ncontrolled pilot protocol\n")
    evidence = workspace / "evidence" / "tenant" / "session" / "evidence.jpg"
    evidence.parent.mkdir(parents=True)
    evidence.write_bytes(b"opaque-evidence")
    (runtime / ".env").write_text("SECRET=excluded\n", encoding="utf-8")
    (runtime / "unallowlisted.json").write_text("{}\n", encoding="utf-8")
    raw_audio = workspace / "diagnostics" / "raw.wav"
    raw_audio.parent.mkdir()
    raw_audio.write_bytes(b"excluded raw audio")
    original_bytes = {path: path.read_bytes() for path in source_databases}

    copied_connections: list[sqlite3.Connection] = []
    sqlite_backup = pilot_state_backup._sqlite_backup

    def backup_with_live_staging_sidecars(source: Path, destination: Path) -> None:
        sqlite_backup(source, destination)
        connection = sqlite3.connect(destination)
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        connection.execute(
            "CREATE TABLE IF NOT EXISTS backup_sidecar_guard(value TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO backup_sidecar_guard VALUES('live')")
        connection.commit()
        connection.execute("SELECT value FROM backup_sidecar_guard").fetchone()
        assert Path(f"{destination}-wal").is_file()
        assert Path(f"{destination}-shm").is_file()
        copied_connections.append(connection)

    monkeypatch.setattr(pilot_state_backup, "_sqlite_backup", backup_with_live_staging_sidecars)
    archive = tmp_path / "candidate-a-backup.tar.gz"
    args = argparse.Namespace(
        protocol_data_dir=str(runtime),
        workspace_data_dir=str(workspace),
        report_database=str(reports),
        output=str(archive),
    )
    try:
        created_manifest = pilot_state_backup.create_backup(args)
    finally:
        for connection in copied_connections:
            connection.close()

    verified_manifest = pilot_state_backup.verify_backup(archive)
    restored = tmp_path / "candidate-a-restored"
    restored_manifest = pilot_state_backup.restore_backup(archive, restored)
    declared = {entry["path"] for entry in created_manifest["files"]}
    expected = {
        "protocol/protocol_workspace.sqlite",
        "protocol/objects/sha256/aa/source.pdf",
        "workspace/commercial_workspace.sqlite",
        "workspace/evidence/tenant/session/evidence.jpg",
        "reports/experiment_reports.sqlite",
    }
    assert declared == expected
    assert verified_manifest == created_manifest
    assert restored_manifest == created_manifest

    with tarfile.open(archive, "r:gz") as bundle:
        archive_files = {member.name for member in bundle if member.isfile()}
        archive_directories = {member.name for member in bundle if member.isdir()}
    assert archive_files == {"manifest.json", *declared}
    assert archive_directories == set()
    assert not any(name.endswith((".sqlite-wal", ".sqlite-shm")) for name in archive_files)
    assert not any(
        marker in name
        for name in archive_files
        for marker in (".env", "raw.wav", "unallowlisted.json")
    )
    assert "protocol/workspace/commercial_workspace.sqlite" not in archive_files
    assert "protocol/experiment_reports.sqlite" not in archive_files

    restored_files = {
        path.relative_to(restored).as_posix()
        for path in restored.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    }
    manifest_on_disk = json.loads(
        (restored / "manifest.json").read_text(encoding="utf-8")
    )
    assert restored_files == declared
    assert {entry["path"] for entry in manifest_on_disk["files"]} == declared
    for database in restored.rglob("*.sqlite"):
        with sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True) as connection:
            assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"

    assert {path: path.read_bytes() for path in source_databases} == original_bytes
    for path, expected_value in zip(
        source_databases,
        ("protocol", "reports", "workspace"),
        strict=True,
    ):
        with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as connection:
            assert connection.execute("SELECT value FROM evidence").fetchone()[0] == expected_value

    for connection in source_connections:
        connection.close()
