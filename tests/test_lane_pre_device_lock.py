"""Lane PRE, decision 3 (human decision 2026-10-24): the login lock is "this name on this device".

Before, five wrong passwords for a name locked that name for fifteen minutes
for everyone, so anybody who knew a labmate's name (it is on the screen)
could lock them out (lane PERSONA, F2). The server sits behind a tunnel or a
proxy, so the peer address cannot tell devices apart; the browser keeps a
device marker instead -- a random cookie (HttpOnly, Secure when it can be,
one year) -- and the lock counts by (name, device):

* five failures for a name from one device within fifteen minutes lock that
  name on that device only, for fifteen minutes;
* thirty failures for a name within fifteen minutes, whatever the devices,
  lock the whole name for fifteen minutes (guessing from many devices);
* a request without the cookie counts as one shared "unknown device";
* the current-password check (password change, account deletion) counts the
  same way;
* the lock record keeps a digest of the marker, never the marker;
* the app's screen (bearer, no cookie) sends its marker in the request body.

Provider calls: none. Network: this process only.
"""

from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path

import httpx

from voiney_lab import accounts as acc
from voiney_lab.server import app

from tests.test_lane_on_accounts_store import _Clock
from tests.test_lane_on_auth_api import Device, PASSWORD, _configure, _invite

DEVICE_COOKIE = "voiney_device"
WRONG = "wrong wrong wrong"
APP = "https://localhost"


# --- The store ------------------------------------------------------------------


class DeviceLockStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.path = Path(self._temp.name) / acc.ACCOUNTS_DATABASE_FILENAME
        self.clock = _Clock()
        self.store = acc.AccountStore.open(self.path, clock=self.clock)
        self.addCleanup(self.store.close)
        token, _ = self.store.create_invite()
        self.owner = self.store.sign_up(token, "김재준", PASSWORD)
        self.owner_device = acc.new_device_marker()
        self.attacker_device = acc.new_device_marker()

    def fail(self, device, name="김재준", times=1):
        for _ in range(times):
            with self.assertRaises(acc.LoginRefusedError):
                self.store.log_in(name, WRONG, device=device)

    def test_the_limits_are_five_per_device_and_thirty_per_name_in_fifteen_minutes(self):
        self.assertEqual(acc.LOGIN_FAILURE_LIMIT, 5)
        self.assertEqual(acc.LOGIN_NAME_FAILURE_LIMIT, 30)
        self.assertEqual(acc.LOGIN_FAILURE_WINDOW.total_seconds(), 15 * 60)
        self.assertEqual(acc.DEVICE_COOKIE_NAME, DEVICE_COOKIE)
        self.assertEqual(acc.DEVICE_COOKIE_LIFETIME.days, 365)

    def test_five_failures_lock_the_name_on_that_device_only(self):
        self.fail(self.attacker_device, times=5)
        with self.assertRaises(acc.LoginLockedError) as caught:
            self.store.log_in("김재준", PASSWORD, device=self.attacker_device)
        self.assertGreater(caught.exception.retry_after_seconds, 0)
        self.assertLessEqual(caught.exception.retry_after_seconds, 15 * 60)
        # The owner's own device still logs in with the right password.
        self.assertEqual(self.store.log_in("김재준", PASSWORD, device=self.owner_device).name, "김재준")
        # So does a device that never failed, and nothing is locked once the window passes.
        self.assertEqual(self.store.log_in("김재준", PASSWORD, device=acc.new_device_marker()).name, "김재준")
        self.clock.advance(minutes=15, seconds=1)
        self.assertEqual(self.store.log_in("김재준", PASSWORD, device=self.attacker_device).name, "김재준")

    def test_the_lock_on_one_device_leaves_other_names_open_on_it(self):
        token, _ = self.store.create_invite()
        self.store.sign_up(token, "최수진", "another pass phrase")
        self.fail(self.attacker_device, times=5)
        self.assertEqual(
            self.store.log_in("최수진", "another pass phrase", device=self.attacker_device).name, "최수진")

    def test_thirty_failures_from_any_devices_lock_the_whole_name_for_fifteen_minutes(self):
        for _ in range(6):
            self.fail(acc.new_device_marker(), times=4)
        self.fail(acc.new_device_marker(), times=5)
        # 29 failures: the owner is still let in.
        self.assertEqual(self.store.log_in("김재준", PASSWORD, device=self.owner_device).name, "김재준")
        self.fail(acc.new_device_marker())
        with self.assertRaises(acc.LoginLockedError) as caught:
            self.store.log_in("김재준", PASSWORD, device=self.owner_device)
        self.assertGreater(caught.exception.retry_after_seconds, 15 * 60 - 5)
        self.assertLessEqual(caught.exception.retry_after_seconds, 15 * 60)
        with self.assertRaises(acc.LoginLockedError):
            self.store.log_in("김재준", PASSWORD, device=acc.new_device_marker())
        self.clock.advance(minutes=15, seconds=1)
        self.assertEqual(self.store.log_in("김재준", PASSWORD, device=self.owner_device).name, "김재준")

    def test_the_name_lock_ends_when_the_failures_that_made_it_age_out(self):
        for _ in range(6):
            self.fail(acc.new_device_marker(), times=5)
            self.clock.advance(minutes=1)
        with self.assertRaises(acc.LoginLockedError) as caught:
            self.store.log_in("김재준", PASSWORD, device=self.owner_device)
        # The first failure was six minutes ago: the count drops under thirty in nine minutes.
        self.assertEqual(caught.exception.retry_after_seconds, 9 * 60)
        self.clock.advance(minutes=9, seconds=1)
        self.assertEqual(self.store.log_in("김재준", PASSWORD, device=self.owner_device).name, "김재준")

    def test_without_a_marker_every_request_is_the_one_unknown_device(self):
        for device in (None, "", "not a marker!", "x" * 300):
            self.fail(device)
        self.fail(None)
        with self.assertRaises(acc.LoginLockedError):
            self.store.log_in("김재준", PASSWORD)
        with self.assertRaises(acc.LoginLockedError):
            self.store.log_in("김재준", PASSWORD, device="")
        self.assertEqual(self.store.log_in("김재준", PASSWORD, device=self.owner_device).name, "김재준")

    def test_a_success_clears_only_this_devices_failures(self):
        self.fail(self.owner_device, times=4)
        self.fail(self.attacker_device, times=4)
        self.store.log_in("김재준", PASSWORD, device=self.owner_device)
        self.fail(self.owner_device, times=4)
        self.assertEqual(self.store.log_in("김재준", PASSWORD, device=self.owner_device).name, "김재준")
        self.fail(self.attacker_device)
        with self.assertRaises(acc.LoginLockedError):
            self.store.log_in("김재준", PASSWORD, device=self.attacker_device)

    def test_the_current_password_check_counts_on_the_same_device(self):
        for _ in range(5):
            with self.assertRaises(acc.LoginRefusedError):
                self.store.change_password(self.owner.account_id, WRONG, "new pass phrase 9", device=self.attacker_device)
        with self.assertRaises(acc.LoginLockedError):
            self.store.change_password(self.owner.account_id, PASSWORD, "new pass phrase 9", device=self.attacker_device)
        with self.assertRaises(acc.LoginLockedError):
            self.store.switch_off_for_deletion(self.owner.account_id, PASSWORD, device=self.attacker_device)
        with self.assertRaises(acc.LoginLockedError):
            self.store.log_in("김재준", PASSWORD, device=self.attacker_device)
        self.store.change_password(self.owner.account_id, PASSWORD, "new pass phrase 9", device=self.owner_device)
        self.assertEqual(self.store.log_in("김재준", "new pass phrase 9", device=self.owner_device).name, "김재준")

    def test_the_lock_record_keeps_a_digest_of_the_marker_never_the_marker(self):
        self.fail(self.attacker_device)
        self.fail(None)
        connection = sqlite3.connect(self.path)
        self.addCleanup(connection.close)
        kept = [row[0] for row in connection.execute("SELECT client FROM login_attempts ORDER BY attempt_id")]
        self.assertEqual(len(kept), 2)
        self.assertNotIn(self.attacker_device, "".join(kept))
        self.assertRegex(kept[0], r"^[0-9a-f]{32}$")
        self.assertNotEqual(kept[0], kept[1])

    def test_a_new_marker_is_random_and_url_safe(self):
        markers = {acc.new_device_marker() for _ in range(50)}
        self.assertEqual(len(markers), 50)
        for marker in markers:
            self.assertRegex(marker, r"^[A-Za-z0-9_-]{32}$")
            self.assertEqual(acc.device_marker(marker), marker)
        for refused in (None, "", 7, "short", "has space in it 0123456789", "x" * 300):
            self.assertIsNone(acc.device_marker(refused))


# --- Over HTTP, as the page does it ------------------------------------------------


def _set_cookies(response, name):
    return [value for key, value in response.headers.multi_items()
            if key.lower() == "set-cookie" and value.startswith(name + "=")]


def _browser(**kwargs) -> Device:
    """A browser that opened the page: the page asks /api/auth/session first."""

    device = Device(**kwargs)
    assert device.get("/api/auth/session").status_code == 200
    return device


def _login(device, password, name="owner", **kwargs):
    return device.post("/api/auth/login", json={"name": name, "password": password}, **kwargs)


def test_the_page_gets_a_device_marker_with_its_first_session_question(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    device = Device()
    first = device.get("/api/auth/session")
    (cookie,) = _set_cookies(first, DEVICE_COOKIE)
    marker = device.client.cookies.get(DEVICE_COOKIE)
    assert acc.device_marker(marker) == marker
    lowered = cookie.lower()
    assert "httponly" in lowered and "samesite=lax" in lowered and "path=/" in lowered
    assert "max-age=31536000" in lowered
    assert "secure" not in lowered, "plain HTTP cannot carry a Secure cookie"
    # Kept, not replaced, while the browser has it.
    assert _set_cookies(device.get("/api/auth/session"), DEVICE_COOKIE) == []
    assert device.client.cookies.get(DEVICE_COOKIE) == marker
    # Behind HTTPS it is Secure.
    secure = Device(headers={"X-Forwarded-Proto": "https"}).get("/api/auth/session")
    assert "secure" in _set_cookies(secure, DEVICE_COOKIE)[0].lower()


def test_a_broken_marker_is_replaced_and_dev_mode_sets_none(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    broken = Device().get("/api/auth/session", headers={"Cookie": f"{DEVICE_COOKIE}=short!"})
    assert len(_set_cookies(broken, DEVICE_COOKIE)) == 1
    _configure(monkeypatch, tmp_path, mode="dev")
    assert _set_cookies(Device().get("/api/auth/session"), DEVICE_COOKIE) == []


def test_signup_and_login_leave_the_marker_too(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    phone = Device()
    phone.sign_up(_invite(tmp_path), "owner")
    assert acc.device_marker(phone.client.cookies.get(DEVICE_COOKIE))
    laptop = Device()
    assert _login(laptop, PASSWORD).status_code == 200
    assert acc.device_marker(laptop.client.cookies.get(DEVICE_COOKIE))


def test_an_attackers_five_guesses_lock_their_device_and_the_owner_still_logs_in(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    owner = Device()
    owner.sign_up(_invite(tmp_path), "owner")
    assert owner.post("/api/auth/logout").status_code == 200
    attacker = _browser()
    for _ in range(5):
        assert _login(attacker, WRONG).status_code == 401
    locked = _login(attacker, PASSWORD)
    assert locked.status_code == 429 and locked.json() == {"detail": "login_locked"}
    assert int(locked.headers["retry-after"]) > 0
    # The owner's browser, with its own marker, logs in with the right password.
    right = _login(owner, PASSWORD)
    assert right.status_code == 200 and right.json()["account"]["name"] == "owner"


def test_thirty_guesses_from_many_devices_lock_the_name_everywhere(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    owner = Device()
    owner.sign_up(_invite(tmp_path), "owner")
    assert owner.post("/api/auth/logout").status_code == 200
    for _ in range(6):
        attacker = _browser()
        for _ in range(5):
            assert _login(attacker, WRONG).status_code == 401
    locked = _login(owner, PASSWORD)
    assert locked.status_code == 429 and locked.json() == {"detail": "login_locked"}
    assert 0 < int(locked.headers["retry-after"]) <= 15 * 60


def test_requests_without_the_cookie_share_one_unknown_device(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    owner = Device()
    owner.sign_up(_invite(tmp_path), "owner")
    assert owner.post("/api/auth/logout").status_code == 200
    for _ in range(5):
        assert _login(Device(), WRONG).status_code == 401
    assert _login(Device(), PASSWORD).status_code == 429
    assert _login(owner, PASSWORD).status_code == 200


def test_wrong_current_passwords_lock_that_device_for_the_name(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    _configure(monkeypatch, workspace)
    shared = Device()
    shared.sign_up(_invite(workspace), "owner")
    phone = _browser()
    assert _login(phone, PASSWORD).status_code == 200
    for _ in range(5):
        refused = shared.post("/api/auth/password", json={"current": WRONG, "new": "different password"})
        assert refused.status_code == 401
    blocked = shared.post("/api/auth/password", json={"current": PASSWORD, "new": "different password"})
    assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) > 0
    assert shared.post("/api/auth/delete-account", json={"password": PASSWORD}).status_code == 429
    # The owner's other device changes it.
    changed = phone.post("/api/auth/password", json={"current": PASSWORD, "new": "different password"})
    assert changed.status_code == 200 and changed.json() == {"changed": True}


def _app_login(password, device=None):
    body = {"name": "owner", "password": password}
    if device is not None:
        body["device"] = device

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://pilot.test") as client:
            return await client.post("/api/auth/login", json=body, headers={"Origin": APP})

    return asyncio.run(go())


def test_the_apps_login_sends_its_marker_in_the_body_and_counts_the_same_way(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    monkeypatch.setenv("VOINEY_LAB_APP_ORIGINS", APP)
    Device().sign_up(_invite(tmp_path), "owner")
    thief, mine = acc.new_device_marker(), acc.new_device_marker()
    for _ in range(5):
        assert _app_login(WRONG, thief).status_code == 401
    assert _app_login(PASSWORD, thief).status_code == 429
    answered = _app_login(PASSWORD, mine)
    assert answered.status_code == 200 and answered.json()["app_session"]["token"]
    assert _set_cookies(answered, DEVICE_COOKIE) == [], "the app keeps its own marker; no cookie"
    # Without a marker the app is the unknown device.
    for _ in range(5):
        assert _app_login(WRONG).status_code == 401
    assert _app_login(PASSWORD).status_code == 429
    assert _app_login(PASSWORD, mine).status_code == 200
