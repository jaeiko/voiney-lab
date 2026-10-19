"""Lane FX4, decision 3 (2026-10-19): an admin account opens the pilot metrics without the token.

Lane LOG's check for ``GET /api/admin/pilot-metrics`` looked for an account
role "admin", which no account has: lane ON knows an administrator by the
account behind the request (``_REQUEST_ACCOUNT``, its ``admin`` flag). The
check now asks lane ON's one judgment (``_account_is_admin``); the token
way in is unchanged. Provider calls: none.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from tests.test_lane_on_auth_api import Device, _configure, _invite
from voiney_lab import server as server_module

TOKEN = "lane-fx4-admin-token"
PATH = "/api/admin/pilot-metrics"


def _check(*, account=None, principal=None, token=None) -> None:
    account_token = server_module._REQUEST_ACCOUNT.set(account)
    principal_token = server_module._REQUEST_PRINCIPAL.set(principal)
    try:
        server_module._require_pilot_admin(token)
    finally:
        server_module._REQUEST_PRINCIPAL.reset(principal_token)
        server_module._REQUEST_ACCOUNT.reset(account_token)


def test_an_admin_account_passes_without_the_token(monkeypatch):
    monkeypatch.setenv("VOINEY_LAB_ADMIN_TOKEN", TOKEN)
    _check(account=SimpleNamespace(admin=True))


def test_an_experimenter_account_needs_the_token(monkeypatch):
    monkeypatch.setenv("VOINEY_LAB_ADMIN_TOKEN", TOKEN)
    with pytest.raises(HTTPException) as refused:
        _check(account=SimpleNamespace(admin=False))
    assert refused.value.status_code == 403
    _check(account=SimpleNamespace(admin=False), token=TOKEN)


def test_a_role_named_admin_is_no_longer_read(monkeypatch):
    # The one judgment is lane ON's: a principal's role text is not a permission
    # (identity.Role has only "researcher" since lane DI).
    monkeypatch.setenv("VOINEY_LAB_ADMIN_TOKEN", TOKEN)
    with pytest.raises(HTTPException) as refused:
        _check(principal=SimpleNamespace(roles=(SimpleNamespace(value="admin"),)))
    assert refused.value.status_code == 403


def test_the_token_way_is_unchanged(monkeypatch):
    monkeypatch.delenv("VOINEY_LAB_ADMIN_TOKEN", raising=False)
    with pytest.raises(HTTPException) as closed:
        _check(token=TOKEN)
    assert closed.value.status_code == 503
    monkeypatch.setenv("VOINEY_LAB_ADMIN_TOKEN", TOKEN)
    _check(token=TOKEN)
    with pytest.raises(HTTPException) as wrong:
        _check(token="not-the-token")
    assert wrong.value.status_code == 403


def test_through_the_door_an_admin_account_reads_the_day_table(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    monkeypatch.setenv("VOINEY_LAB_ADMIN_TOKEN", TOKEN)
    admin = Device()
    admin.sign_up(_invite(tmp_path, admin=True), "관리자")
    response = admin.get(PATH)
    assert response.status_code == 200, response.text
    assert response.json()["days"] == []
    researcher = Device()
    researcher.sign_up(_invite(tmp_path), "실험자")
    assert researcher.get(PATH).status_code == 403
    assert researcher.get(PATH, headers={"x-voice-workflow-admin-token": TOKEN}).status_code == 200
