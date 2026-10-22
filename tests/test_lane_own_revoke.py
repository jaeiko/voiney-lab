"""Account events close existing voice connections, including idle sockets."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from tests.test_lane_on_auth_api import Device, PASSWORD, _Socket, _configure, _invite
from voiney_lab import accounts as acc
from voiney_lab import server as srv


@pytest.mark.parametrize("action", ["logout", "password", "disable", "delete", "tool"])
def test_account_event_closes_all_its_sockets(monkeypatch, tmp_path, action):
    _configure(monkeypatch, tmp_path)
    owner, peer, admin = Device(), Device(), Device()
    owner.sign_up(_invite(tmp_path), "owner")
    peer.sign_up(_invite(tmp_path), "peer")
    admin.sign_up(_invite(tmp_path, admin=True), "admin")
    account = srv._account_for_cookie(owner.session_cookie)
    second = Device()
    assert second.post("/api/auth/login", json={"name": "owner", "password": PASSWORD}).status_code == 200

    class IdleSocket(_Socket):
        def __init__(self, cookie):
            super().__init__({acc.SESSION_COOKIE_NAME: cookie})
            self.ready = asyncio.Event()
            self.incoming = asyncio.Queue()

        async def send_text(self, value):
            await super().send_text(value)
            if json.loads(value)["type"] == "ready":
                self.ready.set()

        async def receive(self):
            return await self.incoming.get()

    async def run():
        sockets = [IdleSocket(d.session_cookie) for d in (owner, second, peer)]
        tasks = [asyncio.create_task(srv.voice_socket(s)) for s in sockets]
        try:
            await asyncio.wait_for(asyncio.gather(*(s.ready.wait() for s in sockets)), 5)
            if action == "tool":
                store = acc.AccountStore.open(tmp_path / acc.ACCOUNTS_DATABASE_FILENAME)
                try:
                    store.set_disabled(account.account_id, True)
                finally:
                    store.close()
                await asyncio.wait_for(asyncio.gather(*tasks[:2], return_exceptions=True), 3)
            else:
                if action == "disable":
                    cookie = admin.session_cookie
                    path, body = f"/api/auth/accounts/{account.account_id}/disabled", {"disabled": True}
                else:
                    cookie = owner.session_cookie
                    path = "/api/auth/" + {"logout": "logout", "password": "password", "delete": "delete-account"}[action]
                    body = {"current": PASSWORD, "new": "a different password", "password": PASSWORD}
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url="http://test",
                                             cookies={acc.SESSION_COOKIE_NAME: cookie}) as client:
                    response = await client.post(path, json=body)
                assert response.status_code == 200, response.text
            assert all(s.closed and s.closed[0] == 1008 for s in sockets[:2])
            assert sockets[2].closed is None
            assert all(t.done() for t in tasks[:2])
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    asyncio.run(run())
