"""Lane FX5, decision 4 (2026-10-20): no secret is written down.

An invite token, a session token, the admin token and a password are
credentials. After signing up, logging in, a refused invite, a wrong
password, a wrong or missing admin token, a wrong bearer token and a refused
account deletion, none of them may be found in what the server wrote: its log
records, any response body it sent (a Set-Cookie header carries the session
by design, and only that), the stores (accounts, workspace, experiment
records, pilot metrics) or the deletion record. The invite token also rides
in the URL path of ``GET /api/auth/invites/<token>``, which uvicorn's access
line prints, so the real uvicorn lines are read as well.
Provider calls: none. Network: this process and one loopback socket.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import warnings
from pathlib import Path

import httpx
import pytest

from tests.test_lane_fx5_account_erasure import scan
from tests.test_lane_on_auth_api import PASSWORD, Device, _configure, _invite, _store
from voiney_lab import accounts as acc
from voiney_lab import server as server_module
from voiney_lab.server import voice_socket

NEW_PASSWORD = "an entirely different phrase 91"
WRONG_PASSWORD = "wrong horse staple 1"
ADMIN_TOKEN = "admin-token-4f7c1e9a0b-secret"
WRONG_ADMIN_TOKEN = "wrong-admin-token-77aa-secret"
MADE_UP_INVITE = "made-up-invite-token-3b8d5e-secret"
BEARER = "bearer_token_that_is_wrong_0123456789ABCDEF"
APP_ORIGIN = "https://localhost"
#: The test's own client prints the URLs it asks for and the event loop its selector; neither is the server.
CLIENT_SIDE_LOGGERS = frozenset({"httpx", "httpcore", "asyncio"})


def _walk(value):
    """Every string inside a decoded JSON value, keys included."""

    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _walk(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _walk(item)
    elif isinstance(value, str):
        yield value


class _Wire:
    """Every request a test makes and the answer: what a secret must not be found in."""

    def __init__(self) -> None:
        self.answers: list[str] = []

    def note(self, response: httpx.Response, *, cookie_is_the_session: bool = False) -> httpx.Response:
        headers = [
            f"{key}: {value}" for key, value in response.headers.multi_items()
            if not (cookie_is_the_session and key.lower() == "set-cookie")
        ]
        self.answers.append("\n".join([str(response.status_code), *headers, response.text]))
        return response


@pytest.fixture
def pilot(monkeypatch, tmp_path):
    folder = tmp_path / "pilot"
    workspace = folder / "workspace"
    workspace.mkdir(parents=True)
    _configure(monkeypatch, workspace)
    monkeypatch.setenv("VOINEY_LAB_EXPERIMENT_REPORTS_ENABLED", "true")
    monkeypatch.setenv("VOINEY_LAB_EXPERIMENT_REPORT_DB", str(folder / "experiment_reports.sqlite"))
    monkeypatch.setenv("VOINEY_LAB_ADMIN_TOKEN", ADMIN_TOKEN)
    monkeypatch.setenv("VOINEY_LAB_APP_ORIGINS", APP_ORIGIN)
    return folder


def _everything_written(folder: Path, secrets: dict[str, str], records: list[logging.LogRecord], wire: _Wire):
    """Where each secret is found: log records, answers, stores and files."""

    found: dict[str, set[str]] = {label: set() for label in secrets}
    for record in records:
        if record.name in CLIENT_SIDE_LOGGERS:
            continue
        text = record.getMessage() + " " + " ".join(str(item) for item in (record.args or ())) + " " + (
            "".join(str(item) for item in (record.exc_info or ()) if item is not None))
        for label, secret in secrets.items():
            if secret in text:
                found[label].add(f"log:{record.name}:{record.getMessage()[:80]}")
    for answer in wire.answers:
        for label, secret in secrets.items():
            if secret in answer:
                found[label].add(f"answer:{answer.splitlines()[0]}")
    for label, places in scan(folder, secrets).items():
        found[label].update(f"store:{'/'.join(place)}" for place in places)
    for path in folder.rglob("*"):  # the bytes of every file too: a freed page or a write-ahead log counts
        if path.is_file():
            data = path.read_bytes()
            for label, secret in secrets.items():
                if secret.encode("utf-8") in data:
                    found[label].add(f"file:{path.name}")
    return {label: places for label, places in found.items() if places}


def test_no_credential_is_written_where_it_can_be_read_again(pilot, caplog):
    caplog.set_level(logging.DEBUG)
    workspace = pilot / "workspace"
    wire = _Wire()
    admin_invite = _invite(workspace, admin=True)
    member_invite = _invite(workspace)
    spare_invite = _invite(workspace)
    admin = Device()
    member = Device()
    stranger = Device()

    # Signing up (and an invite that is used up, one made up, a password too short).
    admin_signup = wire.note(admin.post("/api/auth/signup", json={
        "invite": admin_invite, "name": "관리자", "password": PASSWORD}), cookie_is_the_session=True)
    assert admin_signup.status_code == 201
    member_signup = wire.note(member.post("/api/auth/signup", json={
        "invite": member_invite, "name": "최수진", "password": PASSWORD}), cookie_is_the_session=True)
    assert member_signup.status_code == 201
    assert wire.note(stranger.post("/api/auth/signup", json={
        "invite": member_invite, "name": "강이수", "password": WRONG_PASSWORD})).status_code == 404
    assert wire.note(stranger.post("/api/auth/signup", json={
        "invite": MADE_UP_INVITE, "name": "강이수", "password": WRONG_PASSWORD})).status_code == 404
    assert wire.note(stranger.post("/api/auth/signup", json={
        "invite": spare_invite, "name": "강이수", "password": "short"})).status_code == 422
    assert wire.note(stranger.get(f"/api/auth/invites/{spare_invite}")).status_code == 200
    assert wire.note(stranger.get(f"/api/auth/invites/{MADE_UP_INVITE}")).status_code == 404

    # Logging in: right, wrong, an unknown name, and a credential pasted into the name box.
    assert wire.note(Device().post("/api/auth/login", json={
        "name": "최수진", "password": WRONG_PASSWORD})).status_code == 401
    assert wire.note(Device().post("/api/auth/login", json={
        "name": "없는사람", "password": WRONG_PASSWORD})).status_code == 401
    assert wire.note(Device().post("/api/auth/login", json={
        "name": spare_invite, "password": WRONG_PASSWORD})).status_code == 401
    assert wire.note(Device().post("/api/auth/login", json={
        "name": ADMIN_TOKEN, "password": PASSWORD})).status_code == 401
    again = Device()
    assert wire.note(again.post("/api/auth/login", json={
        "name": "최수진", "password": PASSWORD}), cookie_is_the_session=True).status_code == 200

    # Malformed bodies: the answer must not echo what was sent.
    for content in (
        ('{"name": "최수진", "password": "' + PASSWORD + '"').encode(),  # cut off: not JSON
        ('["' + PASSWORD + '"]').encode(),  # JSON, but not an object
    ):
        wire.note(Device().post("/api/auth/login", content=content, headers={"content-type": "application/json"}))

    # The admin token: missing, wrong, right; and a wrong bearer from the app's origin.
    for token in (None, WRONG_ADMIN_TOKEN, ADMIN_TOKEN):
        headers = {} if token is None else {"X-Voice-Workflow-Admin-Token": token}
        wire.note(admin.get("/api/admin/pilot-metrics", headers=headers))
        wire.note(stranger.get("/api/admin/metrics", headers=headers))
    wire.note(Device().get("/api/auth/session", headers={
        "origin": APP_ORIGIN, "authorization": f"Bearer {BEARER}"}))
    wire.note(Device().get("/api/workspace/session", headers={
        "origin": APP_ORIGIN, "authorization": f"Bearer {BEARER}"}))

    # Changing the password and refusing to delete the account on a wrong one, then deleting it.
    wire.note(member.post("/api/auth/password", json={"current": WRONG_PASSWORD, "new": NEW_PASSWORD}))
    assert wire.note(member.post("/api/auth/password", json={
        "current": PASSWORD, "new": NEW_PASSWORD})).status_code == 200
    wire.note(member.post("/api/auth/delete-account", json={"password": WRONG_PASSWORD}))
    deleted = wire.note(member.post("/api/auth/delete-account", json={"password": NEW_PASSWORD}))
    assert deleted.status_code == 200

    secrets = {
        "password": PASSWORD, "new password": NEW_PASSWORD, "wrong password": WRONG_PASSWORD,
        "admin invite": admin_invite, "member invite": member_invite, "spare invite": spare_invite,
        "made-up invite": MADE_UP_INVITE, "admin token": ADMIN_TOKEN, "wrong admin token": WRONG_ADMIN_TOKEN,
        "bearer": BEARER,
        "admin session": admin.session_cookie or "no-admin-session", "member session": again.session_cookie or "no-member-session",
    }
    left = _everything_written(pilot, secrets, caplog.records, wire)
    assert left == {}, json.dumps({key: sorted(value) for key, value in left.items()}, ensure_ascii=False, indent=1)


class _AppSocket:
    """The app's voice socket: its Origin header and the one message it sends first."""

    def __init__(self, first):
        self.headers = {"origin": APP_ORIGIN}
        self.cookies = {}
        self.messages = [first]
        self.sent: list[str] = []
        self.closed = None

    async def accept(self):
        pass

    async def send_text(self, value):
        self.sent.append(value)

    async def send_bytes(self, value):
        pass

    async def close(self, code=1000, reason=""):
        self.closed = (code, reason)

    async def receive(self):
        if self.messages:
            return self.messages.pop(0)
        await asyncio.sleep(0.01)
        return {"type": "websocket.disconnect", "code": 1000}


def test_a_wrong_token_on_the_app_socket_is_neither_answered_with_nor_logged(pilot, caplog):
    caplog.set_level(logging.DEBUG)
    first = {"type": "websocket.receive", "text": json.dumps({"type": "client.auth", "token": BEARER})}
    socket = _AppSocket(first)
    asyncio.run(voice_socket(socket))
    assert socket.closed is not None and socket.closed[0] == 1008
    assert BEARER not in "".join(socket.sent)
    assert BEARER not in "\n".join(record.getMessage() for record in caplog.records if record.name not in CLIENT_SIDE_LOGGERS)


def test_the_stores_keep_a_credential_only_as_a_hash(pilot):
    workspace = pilot / "workspace"
    token = _invite(workspace)
    device = Device()
    device.sign_up(token, "최수진")
    session = device.session_cookie
    store = _store(workspace)
    try:
        connection = store._connection  # noqa: SLF001
        (stored_password,) = connection.execute("SELECT password_hash FROM accounts").fetchone()
        (stored_invite,) = connection.execute("SELECT token_sha256 FROM invites").fetchone()
        (stored_session,) = connection.execute("SELECT token_sha256 FROM sessions").fetchone()
    finally:
        store.close()
    assert stored_password.startswith("scrypt$") and PASSWORD not in stored_password
    assert stored_invite == acc._token_hash(token) != token  # noqa: SLF001
    assert stored_session == acc._token_hash(session) != session  # noqa: SLF001


def test_a_failed_login_keeps_no_text_that_was_typed_in_the_name_box(pilot):
    """A person may paste a token or a password into the name box; the lock keeps a digest, not the text."""

    workspace = pilot / "workspace"
    pasted = "pasted-in-the-name-box-by-mistake-Zq81"
    Device().post("/api/auth/login", json={"name": pasted, "password": WRONG_PASSWORD})
    store = _store(workspace)
    try:
        rows = store._connection.execute("SELECT name_key,client FROM login_attempts").fetchall()  # noqa: SLF001
    finally:
        store.close()
    assert len(rows) == 1
    assert pasted.lower() not in rows[0]["name_key"]
    assert pasted not in rows[0]["name_key"]


def test_the_lock_still_counts_by_name_after_the_name_is_kept_as_a_digest(pilot):
    workspace = pilot / "workspace"
    Device().sign_up(_invite(workspace), "최수진")
    for _ in range(acc.LOGIN_FAILURE_LIMIT):
        assert Device().post("/api/auth/login", json={"name": "최수진", "password": WRONG_PASSWORD}).status_code == 401
    locked = Device().post("/api/auth/login", json={"name": "최수진", "password": PASSWORD})
    assert locked.status_code == 429


# --- What uvicorn itself prints ----------------------------------------------------


def test_uvicorn_s_access_line_does_not_carry_an_invite_token():
    import io

    import uvicorn
    from uvicorn.logging import AccessFormatter

    token = "invite-token-in-the-path-9d2f77c1-secret"

    async def stand_in(scope, receive, send):
        if scope["type"] == "http":
            message = await receive()
            while message.get("more_body"):
                message = await receive()
            await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
            await send({"type": "http.response.body", "body": b"ok"})

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(AccessFormatter(
        '%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False))
    logger = logging.getLogger("uvicorn.access")
    saved_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    config = uvicorn.Config(stand_in, host="127.0.0.1", port=0, lifespan="off", log_config=None, access_log=True)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            assert time.monotonic() < deadline and thread.is_alive(), "uvicorn did not start"
            time.sleep(0.01)
        port = server.servers[0].sockets[0].getsockname()[1]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            httpx.get(f"http://127.0.0.1:{port}/api/auth/invites/{token}", timeout=5)
            httpx.get(f"http://127.0.0.1:{port}/api/auth/session", timeout=5)
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        logger.removeHandler(handler)
        logger.setLevel(saved_level)
    text = stream.getvalue()
    assert token not in text, text
    assert '/api/auth/invites/' in text, "the line is still there, without the token"
    assert '"GET /api/auth/session HTTP/1.1" 200' in text, "other paths are logged as they were"
