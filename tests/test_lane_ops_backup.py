"""Daily command boundary with real archive/restore and fake service control."""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.pilot_state_backup import restore_backup

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def daily(tmp_path):
    repo = tmp_path / 'repo'
    (repo / '.venv/bin').mkdir(parents=True)
    (repo / '.venv/bin/python').symlink_to(sys.executable)
    (repo / 'scripts').mkdir()
    (repo / 'scripts/pilot_state_backup.py').symlink_to(ROOT / 'scripts/pilot_state_backup.py')
    protocol = tmp_path / 'protocol'
    workspace = protocol / 'workspace'
    workspace.mkdir(parents=True)
    reports = protocol / 'experiment_reports.sqlite'
    for db_path in (protocol / 'protocol_workspace.sqlite', workspace / 'accounts.sqlite',
                    workspace / 'commercial_workspace.sqlite', reports,
                    protocol / 'approved_safety_catalog.sqlite'):
        with sqlite3.connect(db_path) as db:
            db.execute('CREATE TABLE example (value TEXT)')
            db.execute('INSERT INTO example VALUES (?)', ('synthetic record',))
    objects = protocol / 'objects/sha256'
    objects.mkdir(parents=True)
    (objects / 'sample.txt').write_text('synthetic protocol')
    evidence = workspace / 'evidence'
    evidence.mkdir()
    (evidence / 'sample.txt').write_text('synthetic observation')
    (protocol / '.env').write_text('fake secret')
    (protocol / 'diagnostic.wav').write_bytes(b'fake audio')
    commands = tmp_path / 'commands'
    commands.mkdir()
    events = tmp_path / 'events'
    for name, body in {
        'systemctl': 'echo "$*" >> "$EVENTS"\n'
                     'if [[ "$1" == is-active && "${INACTIVE:-}" == 1 ]]; then exit 3; fi\n'
                     'if [[ "$1" == start && "${RESTART_FAIL:-}" == 1 ]]; then exit 1; fi\n',
        'install': 'mkdir -p "${@: -1}"\nchmod 700 "${@: -1}"\n',
        'runuser': 'shift 3\n'
                   'if [[ "${3:-}" == verify && "${VERIFY_FAIL:-}" == 1 ]]; then exit 1; fi\n'
                   'exec "$@"\n',
    }.items():
        path = commands / name
        path.write_text('#!/bin/bash\nset -eu\n' + body)
        path.chmod(0o755)
    backup = tmp_path / 'backups'
    args = ['bash', str(ROOT / 'scripts/daily_backup.sh'), '--repo-dir', str(repo),
            '--protocol-data-dir', str(protocol), '--workspace-data-dir', str(workspace),
            '--report-database', str(reports), '--backup-dir', str(backup)]
    env = dict(os.environ, PATH=str(commands) + ':' + os.environ['PATH'], EVENTS=str(events))
    return args, env, events, backup, reports, tmp_path


def test_daily_bundle_restores_all_stores_and_objects_without_secrets(daily):
    args, env, events, backup, _, tmp_path = daily
    result = subprocess.run(args, env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert events.read_text().splitlines() == ['is-active --quiet voineylab.service',
        'stop voineylab.service', 'start voineylab.service']
    archives = list(backup.glob('*.tar.gz'))
    assert len(archives) == 1 and archives[0].stat().st_mode & 0o077 == 0
    restored = tmp_path / 'restored'
    manifest = restore_backup(archives[0], restored)
    assert {entry['path'] for entry in manifest['files']} == {
        'protocol/protocol_workspace.sqlite', 'protocol/approved_safety_catalog.sqlite',
        'protocol/objects/sha256/sample.txt', 'workspace/accounts.sqlite',
        'workspace/commercial_workspace.sqlite', 'workspace/evidence/sample.txt',
        'reports/experiment_reports.sqlite'}


@pytest.mark.parametrize('failure', ['corrupt', 'missing', 'inactive', 'restart', 'verify'])
def test_daily_failures_are_visible_and_restart_only_an_active_server(daily, failure):
    args, env, events, backup, reports, _ = daily
    if failure == 'corrupt':
        reports.write_bytes(b'not sqlite')
    if failure == 'missing':
        reports.unlink()
    if failure == 'inactive':
        env['INACTIVE'] = '1'
    if failure == 'restart':
        env['RESTART_FAIL'] = '1'
    if failure == 'verify':
        env['VERIFY_FAIL'] = '1'
    result = subprocess.run(args, env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode != 0
    lines = events.read_text().splitlines() if events.exists() else []
    if failure in ('corrupt', 'restart', 'verify'):
        assert lines[-2:] == ['stop voineylab.service', 'start voineylab.service']
    else:
        assert not any(line.startswith(('stop ', 'start ')) for line in lines)
    if failure not in ('restart', 'verify'):
        assert not list(backup.glob('*.tar.gz'))


def test_daily_duplicate_lock_never_stops_the_server(daily):
    import fcntl
    args, env, events, backup, _, _ = daily
    backup.mkdir()
    with (ROOT / 'scripts/daily_backup.sh').open('r') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = subprocess.run(args, env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode != 0
    assert not events.exists()


@pytest.mark.parametrize('target', ['root', 'source', 'parent'])
def test_daily_refuses_noncanonical_or_overlapping_backup_destinations(daily, target):
    args, env, events, _, reports, tmp_path = daily
    destination = {'root': '/tmp/..', 'source': str(reports.parent),
                   'parent': str(tmp_path)}[target]
    args[args.index('--backup-dir') + 1] = destination
    # Fake install only records calls here: even a regressed guard cannot chmod /.
    fake_install = Path(env['PATH'].split(':')[0]) / 'install'
    fake_install.write_text('#!/bin/bash\necho install >> "$EVENTS"\nexit 1\n')
    result = subprocess.run(args, env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode != 0
    assert not events.exists()


@pytest.mark.parametrize('copy_fails', [False, True])
def test_documented_restore_never_restarts_after_a_failed_copy(daily, copy_fails):
    """Execute the copy-paste block with synthetic data and fake privileged commands."""
    import re
    _, env, events, _, reports, tmp_path = daily
    guide = (ROOT / 'docs/DOMAIN_AND_KEYLESS_GUIDE.md').read_text()
    block = next(text for text in re.findall(r'```bash\n(.*?)```', guide, re.S)
                 if "<<'RESTORE_SWITCH'" in text)
    live = reports.parent
    saved = tmp_path / 'saved'
    staged = tmp_path / 'staged'
    staged.mkdir()
    (staged / 'restored.txt').write_text('synthetic restored state')
    block = block.replace('/opt/voineylab/data/runtime/pilot-before-restore-001', str(saved))
    block = block.replace('/opt/voineylab/data/runtime/pilot', str(live))
    block = block.replace('/var/lib/voineylab/pilot-layout-001', str(staged))
    commands = Path(env['PATH'].split(':')[0])
    for name, body in {
        'sudo': 'exec "$@"',
        'chown': 'exit 0',
        'curl': 'exit 0',
        'cp': 'exit 1' if copy_fails else 'exec /bin/cp "$@"',
    }.items():
        path = commands / name
        path.write_text('#!/bin/bash\n' + body + '\n')
        path.chmod(0o755)
    result = subprocess.run(['bash', '-c', block], env=env, capture_output=True,
                            text=True, timeout=60)
    calls = events.read_text().splitlines()
    assert saved.is_dir() and (saved / 'experiment_reports.sqlite').is_file()
    if copy_fails:
        assert result.returncode != 0
        assert 'start voineylab.service' not in calls
    else:
        assert result.returncode == 0, result.stderr
        assert (live / 'restored.txt').is_file()
        assert calls[-1] == 'start voineylab.service'


def test_daily_lock_does_not_truncate_a_symlink_target(daily):
    args, env, _, backup, _, tmp_path = daily
    backup.mkdir()
    preserved = tmp_path / 'preserved.txt'
    preserved.write_text('preserve this file')
    (backup / '.daily.lock').symlink_to(preserved)
    result = subprocess.run(args, env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert preserved.read_text() == 'preserve this file'
