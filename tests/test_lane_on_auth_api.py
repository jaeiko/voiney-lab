"""Lane ON, decision ① (2026-10-18): accounts at the server's boundary.

With ``VOINEY_LAB_AUTH_MODE=accounts`` every ``/api`` request and the
``/ws`` connection need a signed-in account (the health paths and the admin
metrics, which carry their own token, excepted); the session is a cookie
(HttpOnly, SameSite=Lax, Secure on HTTPS); signup goes through an invite
link only; an administrator switches accounts off and sees every experiment
of the lab, while an experimenter sees their own; the reviewer's name of a
review decision or version is the account's name. In the default ``dev``
mode the server behaves as before: the one development identity, no cookie.
Provider calls: none. Network: this process only.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from voiney_lab import accounts as acc
from voiney_lab import server as server_module
from voiney_lab.server import app, voice_socket


PASSWORD = "correct horse battery"


def _configure(monkeypatch, tmp_path, *, mode="accounts", workspace=True):
    monkeypatch.setenv("VOINEY_LAB_WORKSPACE_ENABLED", "true" if workspace else "false")
    monkeypatch.setenv("VOINEY_LAB_WORKSPACE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("VOINEY_LAB_USAGE_SCOPE", "demo")
    monkeypatch.setenv("VOINEY_LAB_EXPERIMENT_REPORTS_ENABLED", "false")
    monkeypatch.setenv("VOINEY_LAB_PROTOCOL_ENABLED", "false")
    if mode is None:
        monkeypatch.delenv("VOINEY_LAB_AUTH_MODE", raising=False)
    else:
        monkeypatch.setenv("VOINEY_LAB_AUTH_MODE", mode)
    for name in ("VOINEY_LAB_OIDC_ISSUER", "VOINEY_LAB_OIDC_AUDIENCE", "VOINEY_LAB_OIDC_JWKS_URL"):
        monkeypatch.delenv(name, raising=False)


def _store(tmp_path) -> acc.AccountStore:
    return acc.AccountStore.open(Path(tmp_path) / acc.ACCOUNTS_DATABASE_FILENAME)


def _invite(tmp_path, **kwargs) -> str:
    store = _store(tmp_path)
    try:
        token, _ = store.create_invite(**kwargs)
        return token
    finally:
        store.close()


class Device:
    """One browser: its cookie jar lives across requests."""

    def __init__(self, headers=None):
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test", headers=headers or {},
        )

    def request(self, method, path, **kwargs):
        return asyncio.run(self.client.request(method, path, **kwargs))

    def get(self, path, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path, **kwargs):
        return self.request("POST", path, **kwargs)

    @property
    def session_cookie(self):
        return self.client.cookies.get(acc.SESSION_COOKIE_NAME)

    def sign_up(self, token, name, password=PASSWORD):
        response = self.post("/api/auth/signup", json={"invite": token, "name": name, "password": password})
        assert response.status_code == 201, response.text
        return response


def _cookie_header(response) -> str:
    return next(
        (value for key, value in response.headers.multi_items()
         if key.lower() == "set-cookie" and value.startswith(acc.SESSION_COOKIE_NAME + "=")),
        "",
    )


# --- The door -----------------------------------------------------------------


def test_without_a_signed_in_account_the_api_and_the_socket_are_closed(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    device = Device()
    for path in ("/api/protocols", "/api/workspace/session", "/api/experimenter/settings", "/api/workspace/experiments"):
        response = device.get(path)
        assert response.status_code == 401, (path, response.text)
        assert response.json() == {"detail": "authentication_required"}
    # The health paths stay open, and say which door is in use.
    assert device.get("/healthz").status_code == 200
    with monkeypatch.context() as readiness:
        readiness.setenv("VOINEY_LAB_USAGE_SCOPE", "source_protocol_only")
        ready = device.get("/readyz")
    assert ready.status_code == 200
    assert ready.json()["capabilities"]["identity_mode"] == "accounts"
    # The session question is answered without a session.
    session = device.get("/api/auth/session")
    assert session.status_code == 200
    assert session.json() == {"mode": "accounts", "signed_in": False, "account": None}
    # The admin metrics keep their own token and are not behind the cookie.
    metrics = device.get("/api/admin/metrics")
    assert metrics.status_code != 401 or metrics.json() != {"detail": "authentication_required"}


def test_accounts_mode_needs_the_workspace(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path, workspace=False)
    response = Device().get("/api/workspace/session")
    assert response.status_code == 503
    assert response.json() == {"detail": "identity_configuration_invalid"}


def test_dev_mode_is_unchanged_and_names_the_development_identity(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path, mode=None)
    device = Device()
    session = device.get("/api/auth/session")
    assert session.status_code == 200
    body = session.json()
    assert body["mode"] == "dev" and body["signed_in"] is True
    assert body["account"]["name"] == "실험자 (개발 로그인)"
    assert body["account"]["admin"] is False
    # No cookie, and the workspace answers as before.
    assert device.session_cookie is None
    workspace = device.get("/api/workspace/session")
    assert workspace.status_code == 200
    assert workspace.json()["authentication_method"] == "development"
    # The account routes say the door is not in use.
    for method, path, payload in (
        ("POST", "/api/auth/login", {"name": "x", "password": "y" * 8}),
        ("POST", "/api/auth/signup", {"invite": "t", "name": "x", "password": "y" * 8}),
        ("POST", "/api/auth/logout", {}),
        ("POST", "/api/auth/password", {"current": "a" * 8, "new": "b" * 8}),
        ("GET", "/api/auth/accounts", None),
    ):
        response = device.request(method, path, json=payload)
        assert response.status_code == 409, (path, response.text)
        assert response.json() == {"detail": "accounts_disabled"}


# --- Signing up and in --------------------------------------------------------


def test_signup_through_an_invite_sets_a_guarded_cookie_and_signs_the_person_in(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    token = _invite(tmp_path, admin=True, organization_id="lab-skku")
    device = Device()
    preview = device.get(f"/api/auth/invites/{token}")
    assert preview.status_code == 200
    assert preview.json()["valid"] is True
    assert preview.json()["organization_id"] == "lab-skku"
    assert preview.json()["admin"] is True
    assert device.get("/api/auth/invites/not-a-token").status_code == 404
    assert device.get("/api/auth/invites/not-a-token").json() == {"detail": "invite_invalid"}

    response = device.sign_up(token, "김재준")
    account = response.json()["account"]
    assert account["name"] == "김재준" and account["admin"] is True and account["entitlement"] == "pilot"
    assert "password" not in json.dumps(response.json())
    cookie = _cookie_header(response)
    assert "HttpOnly" in cookie
    assert "samesite=lax" in cookie.lower()
    assert "Path=/" in cookie
    assert "Max-Age=2592000" in cookie
    assert "Secure" not in cookie, "plain http keeps the cookie usable on a phone over the LAN"
    assert device.session_cookie

    session = device.get("/api/auth/session").json()
    assert session["signed_in"] is True and session["account"]["name"] == "김재준"
    workspace = device.get("/api/workspace/session")
    assert workspace.status_code == 200
    assert workspace.json()["display_name"] == "김재준"
    assert workspace.json()["authentication_method"] == "accounts"
    assert workspace.json()["organization_id"] == "lab-skku"
    assert workspace.json()["principal_id"].startswith("acct-")
    # The invite was for one person.
    again = Device().post("/api/auth/signup", json={"invite": token, "name": "다른 사람", "password": PASSWORD})
    assert again.status_code == 404 and again.json() == {"detail": "invite_invalid"}


def test_behind_https_the_cookie_is_secure(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    token = _invite(tmp_path)
    device = Device(headers={"X-Forwarded-Proto": "https"})
    cookie = _cookie_header(device.sign_up(token, "김재준"))
    assert "Secure" in cookie


def test_signup_refusals_say_why_and_consume_nothing(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    token = _invite(tmp_path)
    device = Device()
    bad_name = device.post("/api/auth/signup", json={"invite": token, "name": "a", "password": PASSWORD})
    assert bad_name.status_code == 422 and bad_name.json() == {"detail": "account_name_invalid"}
    bad_password = device.post("/api/auth/signup", json={"invite": token, "name": "김재준", "password": "short"})
    assert bad_password.status_code == 422 and bad_password.json() == {"detail": "password_invalid"}
    assert device.get(f"/api/auth/invites/{token}").status_code == 200, "a refused signup keeps the invite"
    device.sign_up(token, "김재준")
    other = Device()
    taken = other.post("/api/auth/signup", json={"invite": _invite(tmp_path), "name": "김재준", "password": PASSWORD})
    assert taken.status_code == 409 and taken.json() == {"detail": "account_name_taken"}
    assert other.session_cookie is None


def test_login_logout_and_the_lock_after_five_failures(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    Device().sign_up(_invite(tmp_path), "김재준")
    device = Device()
    wrong = device.post("/api/auth/login", json={"name": "김재준", "password": "wrong wrong wrong"})
    assert wrong.status_code == 401 and wrong.json() == {"detail": "login_refused"}
    assert device.session_cookie is None
    right = device.post("/api/auth/login", json={"name": " 김재준 ", "password": PASSWORD})
    assert right.status_code == 200 and right.json()["account"]["name"] == "김재준"
    assert device.session_cookie and "HttpOnly" in _cookie_header(right)
    assert device.get("/api/workspace/session").status_code == 200

    out = device.post("/api/auth/logout")
    assert out.status_code == 200 and out.json() == {"signed_out": True}
    assert device.session_cookie is None
    assert device.get("/api/workspace/session").status_code == 401
    assert device.get("/api/auth/session").json()["signed_in"] is False

    for _ in range(acc.LOGIN_FAILURE_LIMIT):
        assert device.post("/api/auth/login", json={"name": "김재준", "password": "nope nope nope"}).status_code == 401
    locked = device.post("/api/auth/login", json={"name": "김재준", "password": PASSWORD})
    assert locked.status_code == 429 and locked.json() == {"detail": "login_locked"}
    assert int(locked.headers["retry-after"]) > 0


def test_the_password_is_changed_with_the_current_one_and_other_devices_sign_out(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    phone = Device()
    phone.sign_up(_invite(tmp_path), "김재준")
    laptop = Device()
    assert laptop.post("/api/auth/login", json={"name": "김재준", "password": PASSWORD}).status_code == 200
    refused = phone.post("/api/auth/password", json={"current": "wrong wrong wrong", "new": "new pass phrase 9"})
    assert refused.status_code == 401 and refused.json() == {"detail": "login_refused"}
    short = phone.post("/api/auth/password", json={"current": PASSWORD, "new": "short"})
    assert short.status_code == 422 and short.json() == {"detail": "password_invalid"}
    changed = phone.post("/api/auth/password", json={"current": PASSWORD, "new": "new pass phrase 9"})
    assert changed.status_code == 200 and changed.json() == {"changed": True}
    assert phone.get("/api/workspace/session").status_code == 200
    assert laptop.get("/api/workspace/session").status_code == 401
    assert Device().post("/api/auth/login", json={"name": "김재준", "password": PASSWORD}).status_code == 401
    assert Device().post("/api/auth/login", json={"name": "김재준", "password": "new pass phrase 9"}).status_code == 200


# --- The administrator ----------------------------------------------------------


def test_an_administrator_lists_and_switches_off_accounts_of_the_lab(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    admin = Device()
    admin.sign_up(_invite(tmp_path, admin=True, organization_id="lab-a"), "관리자")
    member = Device()
    member.sign_up(_invite(tmp_path, organization_id="lab-a"), "최수진")
    outsider = Device()
    outsider.sign_up(_invite(tmp_path, organization_id="lab-b"), "강이수")

    refused = member.get("/api/auth/accounts")
    assert refused.status_code == 403 and refused.json() == {"detail": "admin_required"}
    listed = admin.get("/api/auth/accounts")
    assert listed.status_code == 200
    names = [item["name"] for item in listed.json()["accounts"]]
    assert names == ["관리자", "최수진"], "another lab's account is not listed"
    member_id = next(item["account_id"] for item in listed.json()["accounts"] if item["name"] == "최수진")
    outsider_id = outsider.get("/api/auth/session").json()["account"]["account_id"]
    admin_id = admin.get("/api/auth/session").json()["account"]["account_id"]

    off = admin.post(f"/api/auth/accounts/{member_id}/disabled", json={"disabled": True})
    assert off.status_code == 200 and off.json()["account"]["disabled"] is True
    assert member.get("/api/workspace/session").status_code == 401, "the switched-off account's session ended"
    login = Device().post("/api/auth/login", json={"name": "최수진", "password": PASSWORD})
    assert login.status_code == 403 and login.json() == {"detail": "account_disabled"}
    on = admin.post(f"/api/auth/accounts/{member_id}/disabled", json={"disabled": False})
    assert on.status_code == 200 and on.json()["account"]["disabled"] is False
    assert Device().post("/api/auth/login", json={"name": "최수진", "password": PASSWORD}).status_code == 200

    assert admin.post(f"/api/auth/accounts/{outsider_id}/disabled", json={"disabled": True}).status_code == 404
    assert outsider.get("/api/workspace/session").status_code == 200
    me = admin.post(f"/api/auth/accounts/{admin_id}/disabled", json={"disabled": True})
    assert me.status_code == 409 and me.json() == {"detail": "admin_self_disable"}
    assert member.post(f"/api/auth/accounts/{admin_id}/disabled", json={"disabled": True}).status_code in (401, 403)


# --- The voice socket ------------------------------------------------------------


class _Socket:
    def __init__(self, cookies=None):
        self.headers = {}
        self.cookies = dict(cookies or {})
        self.sent = []
        self.closed = None

    async def accept(self):
        pass

    async def send_text(self, value):
        self.sent.append(json.loads(value))

    async def send_bytes(self, value):
        pass

    async def close(self, code=1000, reason=""):
        self.closed = (code, reason)

    async def receive(self):
        await asyncio.sleep(0.01)
        return {"type": "websocket.disconnect", "code": 1000}


def test_the_socket_needs_the_same_cookie(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    device = Device()
    device.sign_up(_invite(tmp_path), "김재준")
    refused = _Socket()
    asyncio.run(voice_socket(refused))
    assert refused.sent and refused.sent[0]["type"] == "error"
    assert refused.sent[0]["message"] == "authentication_required"
    assert refused.closed is not None and refused.closed[0] == 1008
    stale = _Socket({acc.SESSION_COOKIE_NAME: "not-a-session"})
    asyncio.run(voice_socket(stale))
    assert stale.sent[0]["type"] == "error" and stale.closed[0] == 1008
    allowed = _Socket({acc.SESSION_COOKIE_NAME: device.session_cookie})
    asyncio.run(voice_socket(allowed))
    assert allowed.sent and allowed.sent[0]["type"] == "ready", allowed.sent[:2]


@pytest.mark.parametrize("value", ["", "oidc", "both"])
def test_an_unknown_auth_mode_is_refused_by_name(monkeypatch, tmp_path, value):
    _configure(monkeypatch, tmp_path, mode=value)
    response = Device().get("/api/workspace/session")
    if value == "":
        assert response.status_code == 200, "an empty value is the default, dev"
    else:
        assert response.status_code == 503
        assert response.json() == {"detail": "configuration_invalid"}
