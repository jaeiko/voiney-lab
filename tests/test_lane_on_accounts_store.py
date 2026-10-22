"""Lane ON, decision ① (2026-10-18): the account store (``voiney_lab.accounts``).

Human decision: ``VOINEY_LAB_AUTH_MODE`` is ``dev`` (the one development
identity, as before) or ``accounts``. In ``accounts`` a person joins only
through an invite link the server's shell made (expiring, one use), with a
name and a password; afterwards they log in. Principles: passwords are kept
as a hash only (the standard library's scrypt, no new dependency), login
attempts are limited, an account carries one "이용 권한" field (``pilot``;
no payment is built), an administrator can switch an account off, and the
server is the only authority. Nothing here reaches a provider or the network.
"""

from __future__ import annotations

import re
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from voiney_lab.configuration import ConfigurationError
from voiney_lab.identity import Role

from voiney_lab import accounts as acc


class _Clock:
    """A clock the tests move by hand."""

    def __init__(self) -> None:
        self.now = datetime(2026, 10, 18, 9, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now += timedelta(**kwargs)


class _StoreCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.path = Path(self._temp.name) / acc.ACCOUNTS_DATABASE_FILENAME
        self.clock = _Clock()
        self.store = acc.AccountStore.open(self.path, clock=self.clock)
        self.addCleanup(self.store.close)

    def invite(self, **kwargs) -> str:
        token, _ = self.store.create_invite(**kwargs)
        return token

    def join(self, name: str, password: str = "correct horse", **kwargs) -> acc.Account:
        return self.store.sign_up(self.invite(**kwargs), name, password)


class AuthModeSettingTests(unittest.TestCase):
    def test_dev_is_the_default_and_accounts_is_the_other_value(self):
        self.assertEqual(acc.AuthSettings.from_environment({}).mode, "dev")
        self.assertFalse(acc.AuthSettings.from_environment({}).accounts)
        self.assertEqual(acc.AuthSettings.from_environment({"VOINEY_LAB_AUTH_MODE": " Accounts "}).mode, "accounts")
        self.assertTrue(acc.AuthSettings.from_environment({"VOINEY_LAB_AUTH_MODE": "accounts"}).accounts)
        self.assertEqual(acc.AuthSettings.from_environment({"VOINEY_LAB_AUTH_MODE": ""}).mode, "dev")

    def test_another_value_is_refused_by_the_settings_name(self):
        with self.assertRaises(ConfigurationError) as caught:
            acc.AuthSettings.from_environment({"VOINEY_LAB_AUTH_MODE": "oidc"})
        self.assertIn("VOINEY_LAB_AUTH_MODE", str(caught.exception))


class PasswordHashTests(unittest.TestCase):
    def test_only_a_scrypt_hash_is_kept_and_it_verifies(self):
        stored = acc.hash_password("correct horse battery")
        self.assertTrue(stored.startswith("scrypt$"))
        self.assertNotIn("correct horse battery", stored)
        self.assertRegex(stored, r"^scrypt\$\d+\$\d+\$\d+\$[0-9a-f]{32}\$[0-9a-f]{64}$")
        self.assertTrue(acc.verify_password("correct horse battery", stored))
        self.assertFalse(acc.verify_password("correct horse batterx", stored))
        self.assertFalse(acc.verify_password("", stored))

    def test_two_hashes_of_one_password_differ_by_their_salt(self):
        first, second = acc.hash_password("same words"), acc.hash_password("same words")
        self.assertNotEqual(first, second)
        self.assertTrue(acc.verify_password("same words", first) and acc.verify_password("same words", second))

    def test_a_malformed_stored_value_never_verifies(self):
        for stored in ("", "plain", "scrypt$14$8$1$zz$zz", "bcrypt$x"):
            with self.subTest(stored=stored):
                self.assertFalse(acc.verify_password("anything", stored))


class InviteAndSignupTests(_StoreCase):
    def test_a_new_store_is_at_schema_1_with_no_accounts(self):
        self.assertEqual(self.store.schema_version, acc.ACCOUNTS_SCHEMA_VERSION)
        self.assertEqual(self.store.list_accounts(), ())
        self.assertEqual(self.store.list_invites(), ())

    def test_an_invite_is_a_token_kept_only_as_a_hash_with_an_expiry(self):
        token, invite = self.store.create_invite(hours=48, note="첫 참가자")
        self.assertGreaterEqual(len(token), 24)
        self.assertEqual(invite.organization_id, acc.DEFAULT_ORGANIZATION_ID)
        self.assertFalse(invite.admin)
        self.assertEqual(invite.entitlement, "pilot")
        self.assertEqual(invite.expires_at, (self.clock.now + timedelta(hours=48)).isoformat())
        self.assertIsNone(invite.used_at)
        self.assertEqual(invite.note, "첫 참가자")
        rows = sqlite3.connect(self.path).execute("SELECT * FROM invites").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertNotIn(token, "".join(str(value) for value in rows[0]))
        self.assertEqual(self.store.invite_for_token(token).invite_id, invite.invite_id)

    def test_signup_takes_the_invites_lab_and_admin_flag_and_uses_it_once(self):
        token, _ = self.store.create_invite(organization_id="lab-skku-7", admin=True)
        account = self.store.sign_up(token, "  김 재준 ", "correct horse battery")
        self.assertEqual(account.name, "김 재준")
        self.assertEqual(account.organization_id, "lab-skku-7")
        self.assertTrue(account.admin)
        self.assertEqual(account.entitlement, "pilot")
        self.assertFalse(account.disabled)
        self.assertEqual(self.store.list_invites()[0].used_by_account_id, account.account_id)
        with self.assertRaises(acc.InviteInvalidError):
            self.store.sign_up(token, "다른 사람", "correct horse battery")
        with self.assertRaises(acc.InviteInvalidError):
            self.store.invite_for_token(token)

    def test_an_expired_revoked_or_unknown_invite_is_refused(self):
        token, invite = self.store.create_invite(hours=1)
        self.clock.advance(hours=1, seconds=1)
        with self.assertRaises(acc.InviteInvalidError):
            self.store.sign_up(token, "늦은 사람", "correct horse battery")
        other, revoked = self.store.create_invite(hours=24)
        self.assertIsNotNone(self.store.revoke_invite(revoked.invite_id).revoked_at)
        with self.assertRaises(acc.InviteInvalidError):
            self.store.sign_up(other, "취소된 사람", "correct horse battery")
        with self.assertRaises(acc.InviteInvalidError):
            self.store.sign_up("not-a-token", "아무나", "correct horse battery")
        self.assertEqual(self.store.list_accounts(), ())
        self.assertEqual(len(self.store.list_invites()), 2)

    def test_the_name_and_the_password_have_rules(self):
        for bad in ("", " ", "a", "x" * 41, "name\nline", "탭\t이름"):
            with self.subTest(name=bad):
                with self.assertRaises(acc.AccountNameInvalidError):
                    self.store.sign_up(self.invite(), bad, "correct horse battery")
        for bad in ("", "short7!", "x" * 201):
            with self.subTest(password=bad):
                with self.assertRaises(acc.PasswordInvalidError):
                    self.store.sign_up(self.invite(), "유효한 이름", bad)
        # A refused signup consumes nothing.
        self.assertEqual(self.store.list_accounts(), ())
        self.assertTrue(all(invite.used_at is None for invite in self.store.list_invites()))

    def test_a_name_is_taken_once_whatever_its_case_or_spacing(self):
        self.join("Jaejun Kim")
        for taken in ("jaejun kim", "JAEJUN  KIM", " Jaejun Kim "):
            with self.subTest(name=taken):
                with self.assertRaises(acc.AccountNameTakenError):
                    self.join(taken)
        self.assertEqual(len(self.store.list_accounts()), 1)

    def test_an_account_is_the_experimenter_principal_of_its_lab(self):
        account = self.join("최수진", organization_id="lab-a")
        principal = account.principal()
        self.assertEqual(principal.principal_id, f"acct-{account.account_id}")
        self.assertEqual(principal.organization_id, "lab-a")
        self.assertEqual(principal.display_name, "최수진")
        self.assertEqual(principal.roles, frozenset({Role.RESEARCHER}))
        self.assertEqual(principal.authentication_method, "accounts")
        self.assertEqual(principal.subject, "account:최수진")
        public = account.public_dict()
        self.assertEqual(public["name"], "최수진")
        self.assertEqual(public["entitlement"], "pilot")
        self.assertNotIn("password_hash", public)

    def test_an_invite_must_name_a_known_entitlement_and_a_sane_lifetime(self):
        with self.assertRaises(acc.AccountError):
            self.store.create_invite(entitlement="premium")
        with self.assertRaises(acc.AccountError):
            self.store.create_invite(hours=0)
        with self.assertRaises(acc.AccountError):
            self.store.create_invite(hours=24 * 366)
        with self.assertRaises(acc.AccountError):
            self.store.create_invite(organization_id="bad id with spaces")


class LoginAndLockTests(_StoreCase):
    def test_login_by_name_and_password_with_a_wrong_one_refused(self):
        account = self.join("김재준", "correct horse battery")
        self.assertEqual(self.store.log_in("김재준", "correct horse battery").account_id, account.account_id)
        self.assertEqual(self.store.log_in(" 김재준 ", "correct horse battery").account_id, account.account_id)
        with self.assertRaises(acc.LoginRefusedError):
            self.store.log_in("김재준", "correct horse batterx")
        with self.assertRaises(acc.LoginRefusedError):
            self.store.log_in("없는 사람", "correct horse battery")

    def test_five_failures_in_fifteen_minutes_lock_the_name_and_say_how_long(self):
        self.join("김재준", "correct horse battery")
        for _ in range(acc.LOGIN_FAILURE_LIMIT):
            with self.assertRaises(acc.LoginRefusedError):
                self.store.log_in("김재준", "wrong wrong wrong")
        with self.assertRaises(acc.LoginLockedError) as caught:
            self.store.log_in("김재준", "correct horse battery")
        self.assertGreater(caught.exception.retry_after_seconds, 0)
        self.assertLessEqual(caught.exception.retry_after_seconds, acc.LOGIN_FAILURE_WINDOW.total_seconds())
        self.clock.advance(minutes=15, seconds=1)
        self.assertEqual(self.store.log_in("김재준", "correct horse battery").name, "김재준")

    def test_failures_from_one_client_leave_other_names_available(self):
        self.join("김재준", "correct horse battery")
        self.join("최수진", "another pass phrase")
        for _ in range(acc.LOGIN_FAILURE_LIMIT):
            with self.assertRaises(acc.LoginRefusedError):
                self.store.log_in("없는 사람", "x" * 12, client="10.0.0.9")
        self.assertEqual(self.store.log_in("최수진", "another pass phrase", client="10.0.0.9").name, "최수진")
        # Another client is not locked.
        self.assertEqual(self.store.log_in("최수진", "another pass phrase", client="10.0.0.10").name, "최수진")

    def test_a_success_clears_the_names_failures(self):
        self.join("김재준", "correct horse battery")
        for _ in range(acc.LOGIN_FAILURE_LIMIT - 1):
            with self.assertRaises(acc.LoginRefusedError):
                self.store.log_in("김재준", "wrong wrong wrong")
        self.store.log_in("김재준", "correct horse battery")
        for _ in range(acc.LOGIN_FAILURE_LIMIT - 1):
            with self.assertRaises(acc.LoginRefusedError):
                self.store.log_in("김재준", "wrong wrong wrong")
        self.assertEqual(self.store.log_in("김재준", "correct horse battery").name, "김재준")

    def test_a_disabled_account_cannot_log_in_and_its_sessions_end(self):
        account = self.join("김재준", "correct horse battery")
        token = self.store.open_session(account)
        self.assertEqual(self.store.account_for_session(token).account_id, account.account_id)
        self.assertTrue(self.store.set_disabled(account.account_id, True).disabled)
        self.assertIsNone(self.store.account_for_session(token))
        with self.assertRaises(acc.AccountDisabledError):
            self.store.log_in("김재준", "correct horse battery")
        self.assertFalse(self.store.set_disabled(account.account_id, False).disabled)
        self.assertEqual(self.store.log_in("김재준", "correct horse battery").name, "김재준")
        self.assertIsNone(self.store.account_for_session(token), "an ended session does not come back")


class SessionTests(_StoreCase):
    def test_a_session_token_is_random_kept_hashed_and_expires(self):
        account = self.join("김재준")
        token = self.store.open_session(account)
        self.assertGreaterEqual(len(token), 32)
        self.assertNotEqual(token, self.store.open_session(account))
        rows = sqlite3.connect(self.path).execute("SELECT * FROM sessions").fetchall()
        self.assertEqual(len(rows), 2)
        self.assertNotIn(token, "".join(str(value) for value in rows[0]) + "".join(str(value) for value in rows[1]))
        self.assertEqual(self.store.account_for_session(token).name, "김재준")
        self.assertIsNone(self.store.account_for_session("not-a-session"))
        self.assertIsNone(self.store.account_for_session(""))
        self.clock.advance(days=30, seconds=1)
        self.assertIsNone(self.store.account_for_session(token))

    def test_logout_ends_one_session_and_leaves_the_others(self):
        account = self.join("김재준")
        phone, laptop = self.store.open_session(account), self.store.open_session(account)
        self.assertTrue(self.store.close_session(phone))
        self.assertFalse(self.store.close_session(phone))
        self.assertIsNone(self.store.account_for_session(phone))
        self.assertEqual(self.store.account_for_session(laptop).name, "김재준")

    def test_changing_the_password_needs_the_current_one_and_ends_the_other_sessions(self):
        account = self.join("김재준", "correct horse battery")
        phone, laptop = self.store.open_session(account), self.store.open_session(account)
        with self.assertRaises(acc.LoginRefusedError):
            self.store.change_password(account.account_id, "wrong current one", "new pass phrase 9", keep_session_token=phone)
        with self.assertRaises(acc.PasswordInvalidError):
            self.store.change_password(account.account_id, "correct horse battery", "short", keep_session_token=phone)
        self.store.change_password(account.account_id, "correct horse battery", "new pass phrase 9", keep_session_token=phone)
        self.assertEqual(self.store.account_for_session(phone).name, "김재준")
        self.assertIsNone(self.store.account_for_session(laptop))
        with self.assertRaises(acc.LoginRefusedError):
            self.store.log_in("김재준", "correct horse battery")
        self.assertEqual(self.store.log_in("김재준", "new pass phrase 9").name, "김재준")


class AdminAndListingTests(_StoreCase):
    def test_accounts_are_listed_by_lab_and_an_account_is_read_by_id_or_name(self):
        a = self.join("김재준", organization_id="lab-a", admin=True)
        b = self.join("최수진", organization_id="lab-a")
        c = self.join("강이수", organization_id="lab-b")
        self.assertEqual([item.name for item in self.store.list_accounts("lab-a")], ["김재준", "최수진"])
        self.assertEqual([item.name for item in self.store.list_accounts()], ["김재준", "최수진", "강이수"])
        self.assertEqual(self.store.account(c.account_id).organization_id, "lab-b")
        self.assertEqual(self.store.account_by_name("최수진").account_id, b.account_id)
        self.assertIsNone(self.store.account_by_name("없는 사람"))
        with self.assertRaises(acc.AccountNotFoundError):
            self.store.account("acct-does-not-exist")
        self.assertTrue(a.admin and not b.admin)

    def test_deleting_an_account_removes_its_rows_and_nothing_else(self):
        a = self.join("김재준")
        b = self.join("최수진")
        self.store.open_session(a)
        self.store.open_session(b)
        with self.assertRaises(acc.LoginRefusedError):
            self.store.log_in("김재준", "wrong wrong wrong")
        removed = self.store.delete_account(a.account_id)
        self.assertEqual(removed["accounts"], 1)
        self.assertEqual(removed["sessions"], 1)
        self.assertEqual([item.name for item in self.store.list_accounts()], ["최수진"])
        connection = sqlite3.connect(self.path)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 1)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM login_attempts").fetchone()[0], 0)
        with self.assertRaises(acc.AccountNotFoundError):
            self.store.delete_account(a.account_id)

    def test_opening_the_store_again_keeps_everything(self):
        account = self.join("김재준", "correct horse battery")
        token = self.store.open_session(account)
        self.store.close()
        again = acc.AccountStore.open(self.path, clock=self.clock)
        self.addCleanup(again.close)
        self.assertEqual(again.account_for_session(token).name, "김재준")
        self.assertEqual(again.log_in("김재준", "correct horse battery").account_id, account.account_id)

    def test_a_store_of_another_schema_is_refused(self):
        self.store.close()
        connection = sqlite3.connect(self.path)
        connection.executescript(
            "DROP TABLE schema_metadata; CREATE TABLE schema_metadata(schema_version INTEGER);"
            " INSERT INTO schema_metadata VALUES(99);")
        connection.commit()
        connection.close()
        with self.assertRaises(acc.AccountError):
            acc.AccountStore.open(self.path, clock=self.clock)


class NameRulesTests(unittest.TestCase):
    def test_names_are_collapsed_and_compared_without_case(self):
        self.assertEqual(acc.normalize_name("  김  재준 "), "김 재준")
        self.assertEqual(acc.name_key("Jaejun  KIM"), "jaejun kim")
        self.assertTrue(re.fullmatch(r"[0-9a-f]{32}", acc.hash_password("x" * 8).split("$")[4]))


if __name__ == "__main__":
    unittest.main()
