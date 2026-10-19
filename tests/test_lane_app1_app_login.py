"""Lane APP1, decision 3 (2026-10-19): logging in from the app's bundled screen.

A screen bundled in the app runs at its own origin (``https://localhost`` on
Android, ``capacitor://localhost`` on iOS), so the login cookie
(``voiney_session``: HttpOnly, SameSite=Lax) is never sent to the server. For
an origin listed in ``VOINEY_LAB_APP_ORIGINS`` -- and only for one -- the
server answers a login or signup with the session token in the body (no
cookie), takes it back in an ``Authorization: Bearer`` header on ``/api``,
and as the first message on ``/ws`` (``{"type": "client.auth", "token": ...}``);
never in a URL. It is the same session the cookie names: the same lifetime,
logout and password change end it, an administrator switching the account off
ends it. The web keeps its cookie exactly as before, and with the setting
empty (the default) nothing changes at all. The token is never logged.
Provider calls: none. Network: this process only.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import httpx
import pytest

from voiney_lab import accounts as acc
from voiney_lab.configuration import ConfigurationError
from voiney_lab.server import app, voice_socket

PASSWORD = "correct horse battery"
APP = "https://localhost"
IOS_APP = "capacitor://localhost"


def _configure(monkeypatch, tmp_path, *, app_origins: str | None = f"{APP},{IOS_APP}", mode="accounts"):
    monkeypatch.setenv("VOINEY_LAB_WORKSPACE_ENABLED", "true")
    monkeypatch.setenv("VOINEY_LAB_WORKSPACE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("VOINEY_LAB_USAGE_SCOPE", "demo")
    monkeypatch.setenv("VOINEY_LAB_EXPERIMENT_REPORTS_ENABLED", "false")
    monkeypatch.setenv("VOINEY_LAB_PROTOCOL_ENABLED", "false")
    monkeypatch.setenv("VOINEY_LAB_AUTH_MODE", mode)
    if app_origins is None:
        monkeypatch.delenv("VOINEY_LAB_APP_ORIGINS", raising=False)
    else:
        monkeypatch.setenv("VOINEY_LAB_APP_ORIGINS", app_origins)
    for name in ("VOINEY_LAB_OIDC_ISSUER", "VOINEY_LAB_OIDC_AUDIENCE", "VOINEY_LAB_OIDC_JWKS_URL"):
        monkeypatch.delenv(name, raising=False)


def _invite(tmp_path) -> str:
    store = acc.AccountStore.open(Path(tmp_path) / acc.ACCOUNTS_DATABASE_FILENAME)
    try:
        token, _ = store.create_invite()
        return token
    finally:
        store.close()


class Client:
    """A browser (cookie jar) or the app's screen (Origin header, no cookies of ours)."""

    def __init__(self, headers=None):
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://pilot.test", headers=headers or {},
        )

    def request(self, method, path, **kwargs):
        return asyncio.run(self.client.request(method, path, **kwargs))

    def get(self, path, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path, **kwargs):
        return self.request("POST", path, **kwargs)


def _set_cookies(response) -> list[str]:
    return [value for key, value in response.headers.multi_items() if key.lower() == "set-cookie"]


def _sign_up(tmp_path, client, name="김재준"):
    response = client.post("/api/auth/signup", json={"invite": _invite(tmp_path), "name": name, "password": PASSWORD})
    assert response.status_code == 201, response.text
    return response


# --- The settings and the small readers ---------------------------------------------


def test_app_origins_are_exact_origins_and_empty_by_default():
    assert acc.AppAccessSettings.from_environment({}).origins == ()
    settings = acc.AppAccessSettings.from_environment(
        {"VOINEY_LAB_APP_ORIGINS": " https://localhost , Capacitor://LOCALHOST/ ,http://localhost:8100"})
    assert settings.origins == ("https://localhost", "capacitor://localhost", "http://localhost:8100")
    assert settings.allows("https://localhost")
    assert settings.allows("CAPACITOR://localhost")
    assert not settings.allows("https://localhost:8443")
    assert not settings.allows("https://pilot.test")
    assert not settings.allows(None) and not settings.allows("") and not settings.allows("null")
    for bad in ("*", "null", "https://localhost/app", "localhost", "https://", "https://a.test?x=1", "https://user@a.test"):
        with pytest.raises(ConfigurationError):
            acc.AppAccessSettings.from_environment({"VOINEY_LAB_APP_ORIGINS": bad})


def test_the_bearer_and_the_socket_message_are_read_strictly():
    token = "A" * 43
    assert acc.bearer_token(f"Bearer {token}") == token
    assert acc.bearer_token(f"bearer   {token}") == token
    for header in (None, "", "Bearer", "Basic abc", f"Bearer {token} extra", "Bearer a+b/c", f"Bearer {'x' * 300}"):
        assert acc.bearer_token(header) is None, header
    assert acc.socket_auth_token(json.dumps({"type": "client.auth", "token": token})) == token
    for raw in (
        "", "not json", json.dumps({"type": "session.start", "token": token}), json.dumps({"type": "client.auth"}),
        json.dumps({"type": "client.auth", "token": 7}), json.dumps(["client.auth", token]), b"\x00\x01",
    ):
        assert acc.socket_auth_token(raw) is None, raw


# --- HTTP ---------------------------------------------------------------------------


def test_the_app_logs_in_with_a_token_in_the_body_and_no_cookie(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    _sign_up(tmp_path, Client())
    phone = Client({"Origin": APP})
    login = phone.post("/api/auth/login", json={"name": "김재준", "password": PASSWORD})
    assert login.status_code == 200, login.text
    session = login.json()["app_session"]
    assert isinstance(session["token"], str) and len(session["token"]) >= 40
    assert session["expires_in_seconds"] == int(acc.SESSION_LIFETIME.total_seconds())
    assert _set_cookies(login) == [], "a cross-origin cookie would be dropped anyway; none is sent"
    bearer = {"Authorization": f"Bearer {session['token']}"}
    # The bearer opens /api from the app's origin ...
    assert phone.get("/api/workspace/session", headers=bearer).status_code == 200
    who = phone.get("/api/auth/session", headers=bearer).json()
    assert who["signed_in"] is True and who["account"]["name"] == "김재준"
    # ... and from nowhere else: a bearer is not the web's door.
    assert Client().get("/api/workspace/session", headers=bearer).status_code == 401
    assert Client({"Origin": "https://evil.test"}).get("/api/workspace/session", headers=bearer).status_code == 401
    # Logout ends that session; the token opens nothing afterwards.
    assert phone.post("/api/auth/logout", headers=bearer).status_code == 200
    assert phone.get("/api/workspace/session", headers=bearer).status_code == 401
    assert phone.get("/api/auth/session", headers=bearer).json()["signed_in"] is False


def test_signup_from_the_app_gets_the_token_too(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    response = _sign_up(tmp_path, Client({"Origin": IOS_APP}))
    assert _set_cookies(response) == []
    token = response.json()["app_session"]["token"]
    headers = {"Origin": IOS_APP, "Authorization": f"Bearer {token}"}
    assert Client(headers).get("/api/experimenter/settings").status_code == 200


def test_the_web_keeps_its_cookie_and_never_sees_a_token(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    browser = Client({"Origin": "https://pilot.test"})
    signed = _sign_up(tmp_path, browser)
    assert "app_session" not in signed.json()
    login = browser.post("/api/auth/login", json={"name": "김재준", "password": PASSWORD})
    assert "app_session" not in login.json()
    cookies = _set_cookies(login)
    assert len(cookies) == 1 and "HttpOnly" in cookies[0] and "samesite=lax" in cookies[0].lower()
    assert browser.get("/api/workspace/session").status_code == 200


def test_with_the_setting_empty_nothing_changes(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path, app_origins=None)
    _sign_up(tmp_path, Client())
    phone = Client({"Origin": APP})
    login = phone.post("/api/auth/login", json={"name": "김재준", "password": PASSWORD})
    assert login.status_code == 200
    assert "app_session" not in login.json()
    assert len(_set_cookies(login)) == 1
    # A bearer, even the cookie's own value, is not read.
    token = phone.client.cookies.get(acc.SESSION_COOKIE_NAME)
    assert Client({"Origin": APP, "Authorization": f"Bearer {token}"}).get("/api/workspace/session").status_code == 401


def test_a_password_change_from_the_app_keeps_that_session_and_ends_the_others(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    browser = Client()
    _sign_up(tmp_path, browser)
    phone = Client({"Origin": APP})
    token = phone.post("/api/auth/login", json={"name": "김재준", "password": PASSWORD}).json()["app_session"]["token"]
    bearer = {"Authorization": f"Bearer {token}"}
    changed = phone.post("/api/auth/password", headers=bearer, json={"current": PASSWORD, "new": "another long secret"})
    assert changed.status_code == 200, changed.text
    assert phone.get("/api/workspace/session", headers=bearer).status_code == 200
    assert browser.get("/api/workspace/session").status_code == 401


def test_a_switched_off_account_loses_its_app_session(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    _sign_up(tmp_path, Client())
    phone = Client({"Origin": APP})
    login = phone.post("/api/auth/login", json={"name": "김재준", "password": PASSWORD}).json()
    store = acc.AccountStore.open(Path(tmp_path) / acc.ACCOUNTS_DATABASE_FILENAME)
    try:
        store.set_disabled(login["account"]["account_id"], True)
    finally:
        store.close()
    bearer = {"Authorization": f"Bearer {login['app_session']['token']}"}
    assert phone.get("/api/workspace/session", headers=bearer).status_code == 401


def test_the_token_is_never_logged(monkeypatch, tmp_path, caplog):
    _configure(monkeypatch, tmp_path)
    caplog.set_level(logging.DEBUG)
    _sign_up(tmp_path, Client())
    phone = Client({"Origin": APP})
    token = phone.post("/api/auth/login", json={"name": "김재준", "password": PASSWORD}).json()["app_session"]["token"]
    phone.get("/api/workspace/session", headers={"Authorization": f"Bearer {token}"})
    socket = _Socket({"origin": APP}, [{"type": "websocket.receive", "text": json.dumps({"type": "client.auth", "token": token})}])
    asyncio.run(voice_socket(socket))
    assert socket.sent and socket.sent[0]["type"] == "ready"
    assert token not in caplog.text


# --- The voice socket -----------------------------------------------------------------


class _Socket:
    """The app's socket: its Origin header, no cookies, and the messages it sends."""

    def __init__(self, headers=None, messages=None, cookies=None, wait=False):
        self.headers = dict(headers or {})
        self.cookies = dict(cookies or {})
        self.messages = list(messages or [])
        self.wait = wait
        self.received = 0
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
        self.received += 1
        if self.messages:
            return self.messages.pop(0)
        if self.wait:
            await asyncio.sleep(3600)
        await asyncio.sleep(0.01)
        return {"type": "websocket.disconnect", "code": 1000}


def _app_token(tmp_path) -> str:
    _sign_up(tmp_path, Client())
    response = Client({"Origin": APP}).post("/api/auth/login", json={"name": "김재준", "password": PASSWORD})
    return response.json()["app_session"]["token"]


def _auth(token):
    return {"type": "websocket.receive", "text": json.dumps({"type": "client.auth", "token": token})}


def test_the_app_socket_signs_in_with_its_first_message(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    token = _app_token(tmp_path)
    socket = _Socket({"origin": APP}, [_auth(token)])
    asyncio.run(voice_socket(socket))
    assert socket.sent and socket.sent[0]["type"] == "ready", socket.sent[:2]
    assert socket.closed is None or socket.closed[0] != 1008


@pytest.mark.parametrize("first", [
    _auth("not-a-session"),
    {"type": "websocket.receive", "text": json.dumps({"type": "session.start", "language": "ko", "protocol_id": None})},
    {"type": "websocket.receive", "bytes": b"\x00" * 640},
    {"type": "websocket.disconnect", "code": 1000},
])
def test_any_other_first_message_closes_the_app_socket(monkeypatch, tmp_path, first):
    _configure(monkeypatch, tmp_path)
    _app_token(tmp_path)
    socket = _Socket({"origin": APP}, [first])
    asyncio.run(voice_socket(socket))
    assert all(item["type"] != "ready" for item in socket.sent)
    assert socket.closed is not None and socket.closed[0] == 1008


def test_an_app_socket_that_says_nothing_is_closed_after_the_wait(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    monkeypatch.setattr(acc, "APP_SOCKET_AUTH_TIMEOUT_SECONDS", 0.05)
    socket = _Socket({"origin": APP}, wait=True)
    asyncio.run(asyncio.wait_for(voice_socket(socket), 5))
    assert socket.sent[0] == {"type": "error", "message": "authentication_required"}
    assert socket.closed[0] == 1008


def test_without_a_listed_origin_the_socket_does_not_wait_for_a_token(monkeypatch, tmp_path):
    """The web's socket and an unlisted origin are refused at once, as before."""

    _configure(monkeypatch, tmp_path)
    token = _app_token(tmp_path)
    # Another site is refused by its origin (decision 4) before any login.
    for headers, refusal in (({}, "authentication_required"), ({"origin": "https://evil.test"}, "origin_not_allowed")):
        socket = _Socket(headers, [_auth(token)])
        asyncio.run(voice_socket(socket))
        assert socket.received == 0
        assert socket.sent[0]["message"] == refusal and socket.closed[0] == 1008
    _configure(monkeypatch, tmp_path, app_origins=None)
    socket = _Socket({"origin": APP}, [_auth(token)])
    asyncio.run(voice_socket(socket))
    assert socket.received == 0 and socket.closed[0] == 1008


def test_the_cookie_still_opens_the_socket(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    browser = Client()
    _sign_up(tmp_path, browser)
    socket = _Socket({}, cookies={acc.SESSION_COOKIE_NAME: browser.client.cookies.get(acc.SESSION_COOKIE_NAME)})
    asyncio.run(voice_socket(socket))
    assert socket.sent[0]["type"] == "ready"


def test_dev_mode_ignores_all_of_it(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path, mode="dev")
    phone = Client({"Origin": APP})
    assert phone.post("/api/auth/login", json={"name": "x", "password": "y" * 8}).status_code == 409
    socket = _Socket({"origin": APP})
    asyncio.run(voice_socket(socket))
    assert socket.sent[0]["type"] == "ready"
