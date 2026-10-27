"""Default-screen preparation; every switch stays opt-in and reversible."""
from __future__ import annotations
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from voiney_lab import server
from tests.test_lane_rx0_new_ui import _Build

@pytest.mark.parametrize('path', ['/', '/index.html', '/index.html?invite=synthetic'])
def test_old_entry_redirects_only_when_enabled_and_built(monkeypatch: pytest.MonkeyPatch, path: str) -> None:
    monkeypatch.setenv('VOINEY_LAB_NEW_UI', 'true')
    with _Build() as build, patch.object(server, 'NEW_UI_DIR', build):
        r = TestClient(server.app).get(path, follow_redirects=False)
        assert r.status_code == 302
        assert r.headers['location'] == '/app/' + ('?invite=synthetic' if '?' in path else '')
        assert 'no-store' in r.headers['cache-control']

@pytest.mark.parametrize('enabled', ['true', 'false'])
def test_explicit_legacy_escape_always_works(monkeypatch: pytest.MonkeyPatch, enabled: str) -> None:
    monkeypatch.setenv('VOINEY_LAB_NEW_UI', enabled)
    with _Build() as build, patch.object(server, 'NEW_UI_DIR', build):
        page = TestClient(server.app).get('/index.html?legacy=1', follow_redirects=False)
        assert page.status_code == 200
        assert 'tab-records' in page.text

@pytest.mark.parametrize('enabled,expected', [('true', '/app/'), ('false', '/')])
def test_install_manifest_follows_switch_without_changing_installed_identity(monkeypatch: pytest.MonkeyPatch, enabled: str, expected: str) -> None:
    monkeypatch.setenv('VOINEY_LAB_NEW_UI', enabled)
    with _Build() as build, patch.object(server, 'NEW_UI_DIR', build):
        r = TestClient(server.app).get('/manifest.webmanifest')
        body = r.json()
        assert body['id'] == '/'
        assert body['start_url'] == expected
        assert all(icon['src'].startswith('/') for icon in body['icons'])
        assert 'no-cache' in r.headers['cache-control']


def test_missing_build_and_switch_off_keep_old_index(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for flag in ('true', 'false'):
        monkeypatch.setenv('VOINEY_LAB_NEW_UI', flag)
        with patch.object(server, 'NEW_UI_DIR', tmp_path / 'missing'):
            assert TestClient(server.app).get('/index.html', follow_redirects=False).status_code == 200
            assert TestClient(server.app).get('/manifest.webmanifest').json()['start_url'] == '/'
