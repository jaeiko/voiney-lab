"""HTTP revocation joins an existing voice report write before erasing data."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from types import SimpleNamespace

import httpx
import pytest

from tests.lane_n_support import notes_fixture
from tests.test_lane_on_auth_api import Device, PASSWORD, _Socket, _configure, _invite
from voiney_lab import accounts as acc
from voiney_lab import server as srv


@pytest.mark.parametrize("action", ("delete", "logout", "password", "disable"))
def test_revocation_joins_inflight_report_write(monkeypatch, tmp_path, action):
    _configure(monkeypatch, tmp_path)
    monkeypatch.setenv("VOINEY_LAB_EXPERIMENT_REPORTS_ENABLED", "true")
    database = tmp_path / "reports.sqlite"
    monkeypatch.setenv("VOINEY_LAB_EXPERIMENT_REPORT_DB", str(database))
    owner, admin = Device(), Device()
    owner.sign_up(_invite(tmp_path), "owner")
    admin.sign_up(_invite(tmp_path, admin=True), "admin")
    account_id = srv._account_for_cookie(owner.session_cookie).account_id
    release, finished = threading.Event(), threading.Event()
    errors = []

    class Socket(_Socket):
        def __init__(self):
            super().__init__({acc.SESSION_COOKIE_NAME: owner.session_cookie})
            self.ready = asyncio.Event()
            self.incoming = asyncio.Queue()

        async def send_text(self, value):
            await super().send_text(value)
            if json.loads(value)["type"] == "ready":
                self.ready.set()

        async def receive(self):
            return await self.incoming.get()

    def accept_audio(self, chunk):
        # Fake the audio endpoint, not identity, revocation, report opening or erasure.
        self.active = True
        result = SimpleNamespace(voiced_frames=1, total_frames=1, prefix_frames_retained=0,
            rejection_reason=None, forced=False, utterance=b"\0\0", voiced_level_db=None)
        return [srv.ListenerEvent("speech.end", 1, result, generation=self.generation)]

    async def scenario():
        entered, close_returned = asyncio.Event(), asyncio.Event()
        loop = asyncio.get_running_loop()
        real_close = srv._close_account_voice_connections

        async def close(account):
            await real_close(account)
            close_returned.set()

        async def turn(socket, session, *args, **kwargs):
            curated = srv.CuratedProtocolSession(notes_fixture())

            def persist():
                try:
                    loop.call_soon_threadsafe(entered.set)
                    if not release.wait(5):
                        raise TimeoutError("test writer was not released")
                    srv._open_experiment_report(session, curated)
                except Exception as exc:
                    errors.append(type(exc).__name__)
                finally:
                    finished.set()

            # The same write boundary used by the real report-plan call sites.
            write = getattr(srv, "_voice_report_write", asyncio.to_thread)
            await write(persist)

        monkeypatch.setattr(srv, "_close_account_voice_connections", close)
        monkeypatch.setattr(srv.ListenerSession, "accept_chunk", accept_audio)
        monkeypatch.setattr(srv, "run_turn_safely", turn)
        socket = Socket()
        connection = asyncio.create_task(srv.voice_socket(socket))
        request = None
        try:
            await asyncio.wait_for(socket.ready.wait(), 5)
            await socket.incoming.put({"type": "websocket.receive", "bytes": b"\0\0"})
            await asyncio.wait_for(entered.wait(), 5)
            if action == "disable":
                cookie = admin.session_cookie
                path, body = f"/api/auth/accounts/{account_id}/disabled", {"disabled": True}
            else:
                cookie = owner.session_cookie
                path = "/api/auth/" + {"delete": "delete-account", "logout": "logout", "password": "password"}[action]
                body = {"password": PASSWORD, "current": PASSWORD, "new": "different password"}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url="http://test",
                cookies={acc.SESSION_COOKIE_NAME: cookie}) as client:
                request = asyncio.create_task(client.post(path, json=body))
                await asyncio.wait_for(asyncio.gather(connection, return_exceptions=True), 5)
                await asyncio.sleep(0)
                assert not close_returned.is_set(), "erasure/revocation overtook the existing worker"
                assert socket.closed and socket.closed[0] == 1008
                release.set()
                response = await asyncio.wait_for(request, 5)
            assert await asyncio.to_thread(finished.wait, 5)
            assert response.status_code == 200, response.text
            assert errors == []
            with sqlite3.connect(database) as db:
                remaining = db.execute("SELECT COUNT(*) FROM experiment_reports").fetchone()[0]
            assert remaining == (0 if action == "delete" else 1)
            assert account_id not in srv._ACCOUNT_VOICE_REPORT_WRITES
        finally:
            release.set()
            if not connection.done():
                connection.cancel()
            await asyncio.gather(connection, return_exceptions=True)
            if request is not None:
                await asyncio.gather(request, return_exceptions=True)
    asyncio.run(scenario())


def test_cancelled_report_worker_failure_is_retrieved_and_logged_privately(monkeypatch, tmp_path, caplog):
    _configure(monkeypatch, tmp_path)
    owner = Device()
    owner.sign_up(_invite(tmp_path), "owner")
    account = srv._account_for_cookie(owner.session_cookie)
    started, release = threading.Event(), threading.Event()

    def fail():
        started.set()
        if not release.wait(5):
            raise TimeoutError("test writer was not released")
        raise RuntimeError("private report text")

    async def scenario():
        token = srv._REQUEST_ACCOUNT.set(account)
        worker = asyncio.create_task(srv._voice_report_write(fail))
        try:
            assert await asyncio.to_thread(started.wait, 5)
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            release.set()
            await srv._close_account_voice_connections(account.account_id)
            assert account.account_id not in srv._ACCOUNT_VOICE_REPORT_WRITES
        finally:
            release.set()
            await asyncio.gather(worker, return_exceptions=True)
            srv._REQUEST_ACCOUNT.reset(token)
    asyncio.run(scenario())
    assert "voice.report_write_failed error=RuntimeError" in caplog.text
    assert "private report text" not in caplog.text
    assert account.account_id not in caplog.text
