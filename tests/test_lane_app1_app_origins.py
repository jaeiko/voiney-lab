"""Lane APP1, decision 4 (2026-10-19): letting the app's origin in.

``VOINEY_LAB_APP_ORIGINS`` (empty by default: nothing changes) lists the
origins of the app's bundled screen. For them ``/api`` (and the health paths
the screen reads) answers CORS -- the preflight without a session, the
answer's ``Access-Control-Allow-Origin`` echoing the origin, never
``Allow-Credentials`` (the app sends a bearer, not cookies) -- and ``/ws``
accepts them. Once the list is set, ``/ws`` checks the browser's Origin: the
server's own host, a listed app origin, or no Origin at all (not a browser)
pass; any other site is refused before anything else. Before this lane
``/ws`` had no origin check at all; with the list empty it still has none.
Provider calls: none. Network: this process only.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from voiney_lab.server import app, voice_socket

APP = "https://localhost"
IOS_APP = "capacitor://localhost"


def _configure(monkeypatch, tmp_path, *, app_origins: str | None = f"{APP},{IOS_APP}", mode="dev"):
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


def _request(method, path, headers=None, **kwargs):
    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://pilot.test") as client:
            return await client.request(method, path, headers=headers or {}, **kwargs)

    return asyncio.run(go())


def _preflight(origin, path="/api/auth/login", method="POST", headers="content-type"):
    return _request("OPTIONS", path, {
        "Origin": origin, "Access-Control-Request-Method": method, "Access-Control-Request-Headers": headers,
    })


# --- /api ------------------------------------------------------------------------------


def test_the_apps_preflight_is_answered_without_a_session(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path, mode="accounts")
    response = _preflight(APP, headers="authorization, content-type")
    assert response.status_code == 204
    assert response.headers["access-control-allow-origin"] == APP
    assert "POST" in response.headers["access-control-allow-methods"]
    allowed = response.headers["access-control-allow-headers"].lower()
    assert "authorization" in allowed and "content-type" in allowed
    assert "access-control-allow-credentials" not in response.headers
    assert "origin" in response.headers["vary"].lower()
    ios = _preflight(IOS_APP, path="/api/protocols", method="GET", headers="authorization")
    assert ios.status_code == 204 and ios.headers["access-control-allow-origin"] == IOS_APP


def test_answers_to_the_app_carry_the_origin_refusals_included(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path, mode="accounts")
    refused = _request("GET", "/api/workspace/session", {"Origin": APP})
    assert refused.status_code == 401
    assert refused.headers["access-control-allow-origin"] == APP, "the app must be able to read why"
    assert "retry-after" in refused.headers["access-control-expose-headers"].lower()
    ready = _request("GET", "/readyz", {"Origin": APP})
    assert ready.status_code == 200 and ready.headers["access-control-allow-origin"] == APP
    assert "access-control-allow-credentials" not in ready.headers


def test_other_sites_and_other_paths_get_no_cors(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    for origin in ("https://evil.test", "https://localhost:8443", "null"):
        assert "access-control-allow-origin" not in _preflight(origin).headers
        assert "access-control-allow-origin" not in _request("GET", "/api/auth/session", {"Origin": origin}).headers
    # The screen's own files are not an API; the app carries its own copy.
    page = _request("GET", "/", {"Origin": APP})
    assert "access-control-allow-origin" not in page.headers


def test_with_the_setting_empty_nothing_changes(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path, app_origins=None)
    preflight = _preflight(APP)
    assert "access-control-allow-origin" not in preflight.headers
    assert preflight.status_code != 204
    answer = _request("GET", "/api/auth/session", {"Origin": APP})
    assert answer.status_code == 200 and "access-control-allow-origin" not in answer.headers


def test_an_invalid_list_is_not_ready_and_lets_no_app_in(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path, app_origins="*")
    ready = _request("GET", "/readyz")
    assert ready.status_code == 503 and ready.json()["reason"] == "ConfigurationError"
    assert "access-control-allow-origin" not in _preflight(APP).headers


# --- /ws ---------------------------------------------------------------------------------


class _Socket:
    def __init__(self, headers):
        self.headers = dict(headers)
        self.cookies = {}
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


def _open(headers):
    socket = _Socket(headers)
    asyncio.run(voice_socket(socket))
    return socket


@pytest.mark.parametrize("headers", [
    {"origin": APP, "host": "pilot.test"},
    {"origin": "CAPACITOR://localhost", "host": "pilot.test"},
    {"origin": "https://pilot.test", "host": "pilot.test"},
    {"origin": "https://Pilot.Test", "host": "pilot.test"},
    {"origin": "https://pilot.test", "host": "127.0.0.1:8080", "x-forwarded-host": "pilot.test"},
    {"host": "pilot.test"},
])
def test_the_socket_lets_in_the_server_itself_the_app_and_non_browsers(monkeypatch, tmp_path, headers):
    _configure(monkeypatch, tmp_path)
    socket = _open(headers)
    assert socket.sent and socket.sent[0]["type"] == "ready", socket.sent[:1]


@pytest.mark.parametrize("origin", ["https://evil.test", "https://pilot.test.evil.test", "http://localhost", "null"])
def test_the_socket_refuses_any_other_site_once_the_list_is_set(monkeypatch, tmp_path, origin):
    _configure(monkeypatch, tmp_path)
    socket = _open({"origin": origin, "host": "pilot.test"})
    assert socket.sent == [{"type": "error", "message": "origin_not_allowed"}]
    assert socket.closed == (1008, "origin not allowed")


def test_with_the_list_empty_the_socket_checks_no_origin_as_before(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path, app_origins=None)
    socket = _open({"origin": "https://evil.test", "host": "pilot.test"})
    assert socket.sent[0]["type"] == "ready"


def test_an_invalid_list_closes_the_socket(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path, app_origins="https://localhost/app")
    socket = _open({"origin": "https://pilot.test", "host": "pilot.test"})
    assert socket.sent == [{"type": "error", "message": "configuration_invalid"}]
    assert socket.closed[0] == 1008
