"""Accounts for the pilot: invite-only signup, login, sessions (lane ON, decision ①, 2026-10-18).

``VOINEY_LAB_AUTH_MODE`` is ``dev`` (the default: the one development
identity, as before) or ``accounts``. In ``accounts`` a person joins only
through an invite link the server's shell made (``scripts/pilot_invite.py``:
one use, expiring), with a name and a password, and logs in with them
afterwards. This module is the store behind that door; ``server.py`` puts
it in front of every ``/api`` request and the ``/ws`` connection.

What is kept, and how:

* a password only as its scrypt hash (the standard library's
  ``hashlib.scrypt``: a 16-byte salt, N=2**14, r=8, p=1), compared in
  constant time; the password itself is never written anywhere;
* a session as the SHA-256 of a random token; the token goes to the browser
  as an HttpOnly cookie (``SESSION_COOKIE_NAME``) and is seen again only in
  that cookie -- or, for the app's bundled screen at an origin listed in
  ``VOINEY_LAB_APP_ORIGINS`` (lane APP1), in the login answer, and back in an
  ``Authorization: Bearer`` header or the socket's first message; a session lasts ``SESSION_LIFETIME`` and ends at logout, at a
  password change (the other devices' sessions), or when an administrator
  switches the account off;
* an invite as the SHA-256 of its token, with its expiry, the lab it joins
  (``organization_id``), whether the account is an administrator, and its
  entitlement -- one field, ``pilot`` (human decision: no payment is built);
* failed logins by name and by client, so that ``LOGIN_FAILURE_LIMIT``
  failures within ``LOGIN_FAILURE_WINDOW`` lock that name (and that client)
  until the window passes.

An account is the experimenter principal of its lab (``Account.principal``):
``identity.Principal`` with ``authentication_method="accounts"``, so the
workspace store, the catalog and the reports see an account exactly as they
saw the development identity. The store is a SQLite file beside the
workspace's (``ACCOUNTS_DATABASE_FILENAME`` in ``VOINEY_LAB_WORKSPACE_DATA_DIR``),
so the pilot's deletion tool finds it with the rest of the pilot's data.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import secrets
import sqlite3
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from voiney_lab.configuration import ConfigurationError
from voiney_lab.identity import Principal, Role

AUTH_MODE_ENV = "VOINEY_LAB_AUTH_MODE"
AUTH_MODES = ("dev", "accounts")

ACCOUNTS_DATABASE_FILENAME = "accounts.sqlite"
ACCOUNTS_SCHEMA_VERSION = 1
SESSION_COOKIE_NAME = "voiney_session"
SESSION_LIFETIME = timedelta(days=30)
LOGIN_FAILURE_LIMIT = 5
LOGIN_FAILURE_WINDOW = timedelta(minutes=15)
INVITE_DEFAULT_HOURS = 72
INVITE_MAX_HOURS = 24 * 365
ENTITLEMENTS = ("pilot",)
DEFAULT_ENTITLEMENT = "pilot"
DEFAULT_ORGANIZATION_ID = "lab-pilot"
NAME_MIN_CHARACTERS = 2
NAME_MAX_CHARACTERS = 40
PASSWORD_MIN_CHARACTERS = 8
PASSWORD_MAX_CHARACTERS = 200

_SCRYPT_N_LOG2 = 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_LENGTH = 32
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,159}$")
_HEX = re.compile(r"^[0-9a-f]+$")


class AccountError(RuntimeError):
    code = "account_error"


class AccountNameInvalidError(AccountError):
    code = "account_name_invalid"


class AccountNameTakenError(AccountError):
    code = "account_name_taken"


class PasswordInvalidError(AccountError):
    code = "password_invalid"


class InviteInvalidError(AccountError):
    """Unknown, used, expired or revoked: the same word for all four."""

    code = "invite_invalid"


class LoginRefusedError(AccountError):
    """A wrong name or a wrong password: the same word for both."""

    code = "login_refused"


class LoginLockedError(AccountError):
    code = "login_locked"

    def __init__(self, message: str, *, retry_after_seconds: int) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class AccountDisabledError(AccountError):
    code = "account_disabled"


class AdminRequiredError(AccountError):
    code = "admin_required"


class AccountNotFoundError(AccountError):
    code = "account_not_found"


@dataclass(frozen=True)
class AuthSettings:
    mode: str = "dev"

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> "AuthSettings":
        env = os.environ if environment is None else environment
        raw = env.get(AUTH_MODE_ENV, "").strip().casefold() or "dev"
        if raw not in AUTH_MODES:
            raise ConfigurationError(f"{AUTH_MODE_ENV} must be one of: {', '.join(AUTH_MODES)}")
        return cls(raw)

    @property
    def accounts(self) -> bool:
        return self.mode == "accounts"


# --- The app's screen (lane APP1, decisions 3-4, 2026-10-19) ---------------------
#
# A screen bundled in the store app runs at the app's own origin
# (``https://localhost`` on Android, ``capacitor://localhost`` on iOS), so the
# session cookie -- SameSite=Lax, set by the server's origin -- never reaches
# the server from it. For an origin listed in ``VOINEY_LAB_APP_ORIGINS`` the
# server hands the session token back in the login (or signup) answer instead
# of a cookie; the app keeps it in the phone's secure store and sends it in an
# ``Authorization: Bearer`` header on ``/api`` and as the first message on
# ``/ws`` (``{"type": "client.auth", "token": ...}``) -- never in a URL, where
# proxies and access logs would keep it. It is the same session as a cookie's:
# the same lifetime, and logout, a password change or an administrator
# switching the account off end it. Nothing reads a bearer from another
# origin, and with the setting empty (the default) nothing here is used.

APP_ORIGINS_ENV = "VOINEY_LAB_APP_ORIGINS"
APP_SOCKET_AUTH_TYPE = "client.auth"
#: How long an app's socket may take to send its token before it is closed.
APP_SOCKET_AUTH_TIMEOUT_SECONDS = 10.0
#: A session token is secrets.token_urlsafe(32): 43 URL-safe characters.
_SESSION_TOKEN = re.compile(r"^[A-Za-z0-9_-]{20,200}$")
_ORIGIN_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*$")


def normalize_origin(value: object) -> str | None:
    """``scheme://host[:port]`` in lower case, or None when ``value`` is not one origin."""

    if not isinstance(value, str):
        return None
    text = value.strip().casefold().rstrip("/")
    if not text or text == "null" or "*" in text:
        return None
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        return None
    if (
        not _ORIGIN_SCHEME.match(parts.scheme or "")
        or not parts.hostname
        or parts.path or parts.query or parts.fragment
        or "@" in parts.netloc
    ):
        return None
    return f"{parts.scheme}://{parts.hostname}" + (f":{port}" if port is not None else "")


@dataclass(frozen=True)
class AppAccessSettings:
    """The app origins the server lets in (``VOINEY_LAB_APP_ORIGINS``, comma-separated)."""

    origins: tuple[str, ...] = ()

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> "AppAccessSettings":
        env = os.environ if environment is None else environment
        origins: list[str] = []
        for item in env.get(APP_ORIGINS_ENV, "").split(","):
            if not item.strip():
                continue
            origin = normalize_origin(item)
            if origin is None:
                raise ConfigurationError(
                    f"{APP_ORIGINS_ENV} lists exact origins (scheme://host[:port]); no wildcard, path or 'null'")
            if origin not in origins:
                origins.append(origin)
        return cls(tuple(origins))

    def allows(self, origin: object) -> bool:
        normalized = normalize_origin(origin)
        return normalized is not None and normalized in self.origins


def bearer_token(header: object) -> str | None:
    """The token of an ``Authorization: Bearer <token>`` header, or None."""

    if not isinstance(header, str):
        return None
    parts = header.strip().split()
    if len(parts) != 2 or parts[0].casefold() != "bearer" or not _SESSION_TOKEN.match(parts[1]):
        return None
    return parts[1]


def socket_auth_token(raw: object) -> str | None:
    """The token of an app socket's first message ``{"type": "client.auth", "token": ...}``, or None."""

    if not isinstance(raw, str) or not raw:
        return None
    try:
        message = json.loads(raw)
    except (json.JSONDecodeError, RecursionError):
        return None
    if not isinstance(message, dict) or message.get("type") != APP_SOCKET_AUTH_TYPE:
        return None
    token = message.get("token")
    return token if isinstance(token, str) and _SESSION_TOKEN.match(token) else None


# --- Passwords ------------------------------------------------------------------


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    """``scrypt$<log2 N>$<r>$<p>$<salt hex>$<hash hex>``: the only form kept."""

    salt = os.urandom(16) if salt is None else salt
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=1 << _SCRYPT_N_LOG2, r=_SCRYPT_R, p=_SCRYPT_P,
        dklen=_SCRYPT_LENGTH,
    )
    return f"scrypt${_SCRYPT_N_LOG2}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Whether ``password`` is the one hashed into ``stored``; never raises."""

    try:
        kind, n_log2, r, p, salt_hex, digest_hex = str(stored).split("$")
        if kind != "scrypt" or not _HEX.fullmatch(salt_hex) or not _HEX.fullmatch(digest_hex):
            return False
        n_log2, r, p = int(n_log2), int(r), int(p)
        if not 10 <= n_log2 <= 20 or not 1 <= r <= 32 or not 1 <= p <= 16:
            return False
        digest = hashlib.scrypt(
            str(password).encode("utf-8"), salt=bytes.fromhex(salt_hex), n=1 << n_log2, r=r, p=p,
            dklen=len(digest_hex) // 2,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


#: Verified against when the name is unknown, so a wrong name costs the same
#: time as a wrong password.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


# --- Names ----------------------------------------------------------------------


def normalize_name(name: object) -> str:
    """The name as shown: NFC, inner runs of spaces collapsed, trimmed."""

    text = unicodedata.normalize("NFC", str(name if name is not None else ""))
    return re.sub(r" {2,}", " ", text).strip()


def name_key(name: object) -> str:
    """The name as compared: normalized and without case."""

    return normalize_name(name).casefold()


def _checked_name(name: object) -> str:
    cleaned = normalize_name(name)
    if (
        not NAME_MIN_CHARACTERS <= len(cleaned) <= NAME_MAX_CHARACTERS
        or any(unicodedata.category(character)[0] in {"C", "Z"} and character != " " for character in cleaned)
    ):
        raise AccountNameInvalidError("An account name is 2 to 40 printable characters.")
    return cleaned


def _checked_password(password: object) -> str:
    if not isinstance(password, str) or not PASSWORD_MIN_CHARACTERS <= len(password) <= PASSWORD_MAX_CHARACTERS:
        raise PasswordInvalidError("A password is 8 to 200 characters.")
    return password


def _identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise AccountError(f"{label} is malformed.")
    return value


def _token_hash(token: object) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


# --- Records --------------------------------------------------------------------


@dataclass(frozen=True)
class Account:
    account_id: str
    name: str
    organization_id: str
    admin: bool
    entitlement: str
    disabled: bool
    created_at: str

    def principal(self) -> Principal:
        return Principal(
            principal_id=f"acct-{self.account_id}",
            subject=f"account:{self.name}",
            organization_id=self.organization_id,
            display_name=self.name,
            roles=frozenset({Role.RESEARCHER}),
            authentication_method="accounts",
        )

    def public_dict(self) -> dict[str, object]:
        return {
            "account_id": self.account_id,
            "name": self.name,
            "organization_id": self.organization_id,
            "admin": self.admin,
            "entitlement": self.entitlement,
            "disabled": self.disabled,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class Invite:
    invite_id: str
    organization_id: str
    admin: bool
    entitlement: str
    expires_at: str
    used_at: str | None
    used_by_account_id: str | None
    revoked_at: str | None
    created_at: str
    note: str

    def public_dict(self) -> dict[str, object]:
        return {
            "invite_id": self.invite_id,
            "organization_id": self.organization_id,
            "admin": self.admin,
            "entitlement": self.entitlement,
            "expires_at": self.expires_at,
            "used_at": self.used_at,
            "used_by_account_id": self.used_by_account_id,
            "revoked_at": self.revoked_at,
            "created_at": self.created_at,
            "note": self.note,
        }


SCHEMA = """
CREATE TABLE schema_metadata(
 schema_version INTEGER PRIMARY KEY CHECK(schema_version=1)
);
INSERT INTO schema_metadata(schema_version) VALUES(1);
CREATE TABLE accounts(
 account_id TEXT PRIMARY KEY,
 name TEXT NOT NULL,
 name_key TEXT NOT NULL UNIQUE,
 password_hash TEXT NOT NULL,
 organization_id TEXT NOT NULL,
 admin INTEGER NOT NULL CHECK(admin IN (0,1)),
 entitlement TEXT NOT NULL CHECK(entitlement IN ('pilot')),
 disabled INTEGER NOT NULL CHECK(disabled IN (0,1)),
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE TABLE invites(
 invite_id TEXT PRIMARY KEY,
 token_sha256 TEXT NOT NULL UNIQUE,
 organization_id TEXT NOT NULL,
 admin INTEGER NOT NULL CHECK(admin IN (0,1)),
 entitlement TEXT NOT NULL CHECK(entitlement IN ('pilot')),
 expires_at TEXT NOT NULL,
 used_at TEXT,
 used_by_account_id TEXT,
 revoked_at TEXT,
 created_at TEXT NOT NULL,
 note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE sessions(
 session_id TEXT PRIMARY KEY,
 token_sha256 TEXT NOT NULL UNIQUE,
 account_id TEXT NOT NULL REFERENCES accounts(account_id),
 created_at TEXT NOT NULL,
 expires_at TEXT NOT NULL,
 revoked_at TEXT,
 last_seen_at TEXT NOT NULL
);
CREATE INDEX sessions_account ON sessions(account_id);
CREATE TABLE login_attempts(
 attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
 name_key TEXT NOT NULL,
 client TEXT NOT NULL,
 failed_at TEXT NOT NULL
);
CREATE INDEX login_attempts_name ON login_attempts(name_key,failed_at);
CREATE INDEX login_attempts_client ON login_attempts(client,failed_at);
"""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AccountStore:
    """The accounts, invites, sessions and login attempts of one deployment."""

    def __init__(self, connection: sqlite3.Connection, path: Path, *, clock: Callable[[], datetime] | None = None) -> None:
        self._connection = connection
        self.path = path
        self._clock = clock or _utc_now

    @classmethod
    def open(cls, path: Path | str, *, clock: Callable[[], datetime] | None = None) -> "AccountStore":
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        new = not path.exists()
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        try:
            if new:
                connection.executescript(SCHEMA)
                connection.commit()
            row = connection.execute("SELECT schema_version FROM schema_metadata").fetchone()
            version = int(row[0]) if row is not None else None
        except sqlite3.Error as exc:
            connection.close()
            raise AccountError("The account store cannot be read.") from exc
        if version != ACCOUNTS_SCHEMA_VERSION:
            connection.close()
            raise AccountError("The account store's schema is unsupported.")
        return cls(connection, path, clock=clock)

    def close(self) -> None:
        self._connection.close()

    @property
    def schema_version(self) -> int:
        return int(self._connection.execute("SELECT schema_version FROM schema_metadata").fetchone()[0])

    # --- time ---------------------------------------------------------------

    def _now(self) -> datetime:
        now = self._clock()
        return now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)

    @staticmethod
    def _stamp(when: datetime) -> str:
        return when.isoformat()

    @staticmethod
    def _parse(stamp: str) -> datetime:
        return datetime.fromisoformat(stamp)

    # --- invites -------------------------------------------------------------

    def create_invite(
        self, *, organization_id: str = DEFAULT_ORGANIZATION_ID, admin: bool = False,
        entitlement: str = DEFAULT_ENTITLEMENT, hours: float = INVITE_DEFAULT_HOURS, note: str = "",
    ) -> tuple[str, Invite]:
        """A fresh invite: the token (shown once, never kept) and its record."""

        organization_id = _identifier(organization_id, "Lab identifier")
        if entitlement not in ENTITLEMENTS:
            raise AccountError("The entitlement is unknown.")
        try:
            hours = float(hours)
        except (TypeError, ValueError) as exc:
            raise AccountError("The invite lifetime is not a number of hours.") from exc
        if not 0 < hours <= INVITE_MAX_HOURS:
            raise AccountError("The invite lifetime is outside its bounds.")
        token = secrets.token_urlsafe(24)
        now = self._now()
        invite = Invite(
            invite_id="invite-" + secrets.token_hex(8),
            organization_id=organization_id, admin=bool(admin), entitlement=entitlement,
            expires_at=self._stamp(now + timedelta(hours=hours)), used_at=None, used_by_account_id=None,
            revoked_at=None, created_at=self._stamp(now), note=" ".join(str(note or "").split())[:200],
        )
        self._connection.execute(
            """INSERT INTO invites(invite_id,token_sha256,organization_id,admin,entitlement,expires_at,
            used_at,used_by_account_id,revoked_at,created_at,note) VALUES(?,?,?,?,?,?,NULL,NULL,NULL,?,?)""",
            (invite.invite_id, _token_hash(token), invite.organization_id, int(invite.admin), invite.entitlement,
             invite.expires_at, invite.created_at, invite.note),
        )
        self._connection.commit()
        return token, invite

    @staticmethod
    def _invite(row: sqlite3.Row) -> Invite:
        return Invite(
            invite_id=row["invite_id"], organization_id=row["organization_id"], admin=bool(row["admin"]),
            entitlement=row["entitlement"], expires_at=row["expires_at"], used_at=row["used_at"],
            used_by_account_id=row["used_by_account_id"], revoked_at=row["revoked_at"],
            created_at=row["created_at"], note=row["note"],
        )

    def list_invites(self) -> tuple[Invite, ...]:
        rows = self._connection.execute("SELECT * FROM invites ORDER BY rowid").fetchall()
        return tuple(self._invite(row) for row in rows)

    def revoke_invite(self, invite_id: str) -> Invite:
        row = self._connection.execute("SELECT * FROM invites WHERE invite_id=?", (invite_id,)).fetchone()
        if row is None:
            raise InviteInvalidError("The invite is unknown.")
        if row["revoked_at"] is None:
            self._connection.execute(
                "UPDATE invites SET revoked_at=? WHERE invite_id=?", (self._stamp(self._now()), invite_id))
            self._connection.commit()
        return self._invite(self._connection.execute(
            "SELECT * FROM invites WHERE invite_id=?", (invite_id,)).fetchone())

    def _usable_invite_row(self, token: object) -> sqlite3.Row:
        row = self._connection.execute(
            "SELECT * FROM invites WHERE token_sha256=?", (_token_hash(token),)).fetchone()
        if (
            row is None or row["used_at"] is not None or row["revoked_at"] is not None
            or self._parse(row["expires_at"]) <= self._now()
        ):
            raise InviteInvalidError("The invite is unknown, used, revoked or expired.")
        return row

    def invite_for_token(self, token: object) -> Invite:
        """The invite a token opens, or a refusal."""

        return self._invite(self._usable_invite_row(token))

    # --- accounts --------------------------------------------------------------

    @staticmethod
    def _account(row: sqlite3.Row) -> Account:
        return Account(
            account_id=row["account_id"], name=row["name"], organization_id=row["organization_id"],
            admin=bool(row["admin"]), entitlement=row["entitlement"], disabled=bool(row["disabled"]),
            created_at=row["created_at"],
        )

    def sign_up(self, token: object, name: object, password: object) -> Account:
        """Join through an invite: the invite is used up as the account is made."""

        invite = self._usable_invite_row(token)
        cleaned = _checked_name(name)
        secret = _checked_password(password)
        if self._connection.execute(
                "SELECT 1 FROM accounts WHERE name_key=?", (name_key(cleaned),)).fetchone() is not None:
            raise AccountNameTakenError("The name is taken.")
        now = self._stamp(self._now())
        account_id = secrets.token_hex(8)
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            self._connection.execute(
                """INSERT INTO accounts(account_id,name,name_key,password_hash,organization_id,admin,entitlement,
                disabled,created_at,updated_at) VALUES(?,?,?,?,?,?,?,0,?,?)""",
                (account_id, cleaned, name_key(cleaned), hash_password(secret), invite["organization_id"],
                 int(invite["admin"]), invite["entitlement"], now, now),
            )
            used = self._connection.execute(
                """UPDATE invites SET used_at=?,used_by_account_id=? WHERE invite_id=? AND used_at IS NULL""",
                (now, account_id, invite["invite_id"]),
            )
            if used.rowcount != 1:
                raise InviteInvalidError("The invite was used meanwhile.")
            self._connection.commit()
        except sqlite3.IntegrityError as exc:
            self._connection.rollback()
            raise AccountNameTakenError("The name is taken.") from exc
        except Exception:
            self._connection.rollback()
            raise
        return self.account(account_id)

    def account(self, account_id: object) -> Account:
        row = self._connection.execute("SELECT * FROM accounts WHERE account_id=?", (str(account_id),)).fetchone()
        if row is None:
            raise AccountNotFoundError("The account is unknown.")
        return self._account(row)

    def account_by_name(self, name: object) -> Account | None:
        row = self._connection.execute("SELECT * FROM accounts WHERE name_key=?", (name_key(name),)).fetchone()
        return None if row is None else self._account(row)

    def list_accounts(self, organization_id: str | None = None) -> tuple[Account, ...]:
        if organization_id is None:
            rows = self._connection.execute("SELECT * FROM accounts ORDER BY rowid").fetchall()
        else:
            rows = self._connection.execute(
                "SELECT * FROM accounts WHERE organization_id=? ORDER BY rowid",
                (organization_id,)).fetchall()
        return tuple(self._account(row) for row in rows)

    def set_disabled(self, account_id: object, disabled: bool) -> Account:
        """Switch an account off (its sessions end at once) or on again."""

        account = self.account(account_id)
        now = self._stamp(self._now())
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            self._connection.execute(
                "UPDATE accounts SET disabled=?,updated_at=? WHERE account_id=?",
                (int(bool(disabled)), now, account.account_id))
            if disabled:
                self._connection.execute(
                    "UPDATE sessions SET revoked_at=? WHERE account_id=? AND revoked_at IS NULL",
                    (now, account.account_id))
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        return self.account(account.account_id)

    def delete_account(self, account_id: object) -> dict[str, int]:
        """Remove the account and its own rows; the counts removed, by table."""

        account = self.account(account_id)
        counts: dict[str, int] = {}
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            counts["sessions"] = self._connection.execute(
                "DELETE FROM sessions WHERE account_id=?", (account.account_id,)).rowcount
            counts["login_attempts"] = self._connection.execute(
                "DELETE FROM login_attempts WHERE name_key=?", (name_key(account.name),)).rowcount
            self._connection.execute(
                "UPDATE invites SET used_by_account_id=NULL WHERE used_by_account_id=?", (account.account_id,))
            counts["accounts"] = self._connection.execute(
                "DELETE FROM accounts WHERE account_id=?", (account.account_id,)).rowcount
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        return counts

    # --- logging in ----------------------------------------------------------------

    def _failures(self, key_column: str, value: str, since: datetime) -> int:
        return int(self._connection.execute(
            f"SELECT COUNT(*) FROM login_attempts WHERE {key_column}=? AND failed_at>?",
            (value, self._stamp(since))).fetchone()[0])

    def _oldest_failure(self, key_column: str, value: str, since: datetime) -> datetime | None:
        row = self._connection.execute(
            f"SELECT MIN(failed_at) FROM login_attempts WHERE {key_column}=? AND failed_at>?",
            (value, self._stamp(since))).fetchone()
        return None if row is None or row[0] is None else self._parse(row[0])

    def _check_lock(self, key: str, client: str) -> None:
        now = self._now()
        since = now - LOGIN_FAILURE_WINDOW
        for column, value in (("name_key", key), ("client", client)):
            if value and self._failures(column, value, since) >= LOGIN_FAILURE_LIMIT:
                oldest = self._oldest_failure(column, value, since) or now
                remaining = (oldest + LOGIN_FAILURE_WINDOW) - now
                raise LoginLockedError(
                    "Too many failed logins; wait.",
                    retry_after_seconds=max(1, math.ceil(remaining.total_seconds())))

    def _record_failure(self, key: str, client: str) -> None:
        now = self._now()
        self._connection.execute(
            "INSERT INTO login_attempts(name_key,client,failed_at) VALUES(?,?,?)",
            (key, client, self._stamp(now)))
        self._connection.execute(
            "DELETE FROM login_attempts WHERE failed_at<=?", (self._stamp(now - LOGIN_FAILURE_WINDOW),))
        self._connection.commit()

    def log_in(self, name: object, password: object, *, client: str = "") -> Account:
        """The account of a name and password, or one of three refusals."""

        key = name_key(name)
        client = " ".join(str(client or "").split())[:200]
        self._check_lock(key, client)
        row = self._connection.execute("SELECT * FROM accounts WHERE name_key=?", (key,)).fetchone()
        stored = row["password_hash"] if row is not None else _DUMMY_HASH
        if not verify_password(str(password if password is not None else ""), stored) or row is None:
            self._record_failure(key, client)
            raise LoginRefusedError("The name or the password is wrong.")
        account = self._account(row)
        if account.disabled:
            raise AccountDisabledError("The account is switched off.")
        self._connection.execute("DELETE FROM login_attempts WHERE name_key=?", (key,))
        self._connection.commit()
        return account

    def change_password(
        self, account_id: object, current: object, new: object, *, keep_session_token: str | None = None,
    ) -> None:
        """Set a new password after the current one; every other session ends."""

        account = self.account(account_id)
        row = self._connection.execute(
            "SELECT password_hash FROM accounts WHERE account_id=?", (account.account_id,)).fetchone()
        if not verify_password(str(current if current is not None else ""), row["password_hash"]):
            raise LoginRefusedError("The current password is wrong.")
        secret = _checked_password(new)
        now = self._stamp(self._now())
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            self._connection.execute(
                "UPDATE accounts SET password_hash=?,updated_at=? WHERE account_id=?",
                (hash_password(secret), now, account.account_id))
            self._connection.execute(
                "UPDATE sessions SET revoked_at=? WHERE account_id=? AND revoked_at IS NULL AND token_sha256<>?",
                (now, account.account_id, _token_hash(keep_session_token)))
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

    # --- sessions ---------------------------------------------------------------

    def open_session(self, account: Account) -> str:
        """A new session of this account: the token for the cookie."""

        token = secrets.token_urlsafe(32)
        now = self._now()
        self._connection.execute(
            """INSERT INTO sessions(session_id,token_sha256,account_id,created_at,expires_at,revoked_at,last_seen_at)
            VALUES(?,?,?,?,?,NULL,?)""",
            ("session-" + secrets.token_hex(8), _token_hash(token), account.account_id, self._stamp(now),
             self._stamp(now + SESSION_LIFETIME), self._stamp(now)),
        )
        self._connection.commit()
        return token

    def account_for_session(self, token: object) -> Account | None:
        """The account behind a cookie's token, or None when the session is not open."""

        if not token:
            return None
        row = self._connection.execute(
            """SELECT s.session_id,s.expires_at,s.revoked_at,a.* FROM sessions s
            JOIN accounts a ON a.account_id=s.account_id WHERE s.token_sha256=?""",
            (_token_hash(token),)).fetchone()
        if row is None or row["revoked_at"] is not None or self._parse(row["expires_at"]) <= self._now():
            return None
        account = self._account(row)
        if account.disabled:
            return None
        return account

    def close_session(self, token: object) -> bool:
        """End one session (logout); whether one was open."""

        cursor = self._connection.execute(
            "UPDATE sessions SET revoked_at=? WHERE token_sha256=? AND revoked_at IS NULL",
            (self._stamp(self._now()), _token_hash(token)))
        self._connection.commit()
        return cursor.rowcount == 1

    def close_sessions(self, account_id: object) -> int:
        cursor = self._connection.execute(
            "UPDATE sessions SET revoked_at=? WHERE account_id=? AND revoked_at IS NULL",
            (self._stamp(self._now()), str(account_id)))
        self._connection.commit()
        return cursor.rowcount
