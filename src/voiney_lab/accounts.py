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
* failed logins by name and by device (lane PRE, decision 3): the browser
  keeps a random device marker (``DEVICE_COOKIE_NAME``), and
  ``LOGIN_FAILURE_LIMIT`` failures for a name from one device within
  ``LOGIN_FAILURE_WINDOW`` lock that name on that device only, while
  ``LOGIN_NAME_FAILURE_LIMIT`` failures for a name from any devices lock the
  whole name -- each until the window passes. Both are kept as digests.

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
import shutil
import sqlite3
import tempfile
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
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
LOGIN_NAME_FAILURE_LIMIT = 30
LOGIN_FAILURE_WINDOW = timedelta(minutes=15)
DEVICE_COOKIE_NAME = "voiney_device"
DEVICE_COOKIE_LIFETIME = timedelta(days=365)
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
_DEVICE_MARKER = re.compile(r"^[A-Za-z0-9_-]{20,64}$")
#: What a failed login without a device marker is kept under: one shared device.
UNKNOWN_DEVICE = "unknown-device"
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


class LastAdminError(AccountError):
    """The lab's only active administrator cannot leave (lane FX5, decision 2)."""

    code = "last_admin"


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


def _attempt_key(key: str) -> str:
    """What a failed login is kept under in place of the typed name (lane FX5, decision 4).

    The name box may be filled with something that is not a name -- a pasted
    invite or admin token, a password -- and a failed attempt would otherwise
    keep it, in the clear, until the next failure sweeps it. The lock only
    needs to recognise the same text again, so it keeps a digest of it.
    """

    return hashlib.sha256(("login-attempt\0" + key).encode("utf-8")).hexdigest()[:32]


# --- The device a login comes from (lane PRE, decision 3, 2026-10-24) -------------
#
# The server sits behind a tunnel or a proxy, so the peer address does not tell
# one device from another, and a lock by name alone let anybody who knew a
# labmate's name lock them out. A browser therefore keeps a random marker in an
# HttpOnly cookie (``server.py`` sets it with the first session question, at
# signup and at login); the app's screen, which has no cookie, sends its own in
# the login body. The marker is no secret and proves nothing -- it only says
# which failures belong together; a request without one counts as the one
# ``UNKNOWN_DEVICE``. The lock keeps a digest of it, never the marker.


def new_device_marker() -> str:
    """A fresh device marker: 32 URL-safe characters."""

    return secrets.token_urlsafe(24)


def device_marker(value: object) -> str | None:
    """The marker when it has a marker's shape, else None (counted as the unknown device)."""

    return value if isinstance(value, str) and _DEVICE_MARKER.fullmatch(value) else None


def _device_key(device: object) -> str:
    marker = device_marker(device)
    if marker is None:
        return UNKNOWN_DEVICE
    return hashlib.sha256(("login-device\0" + marker).encode("utf-8")).hexdigest()[:32]


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


# ``login_attempts.client`` holds the device's digest (``_device_key``) since lane
# PRE; it held the peer address before, which the lock never read.
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

    def switch_off_for_deletion(self, account_id: object, password: object, *, device: object = None) -> Account:
        """The first step of an account deleting itself (lane FX5, decision 2).

        The password is asked again and checked as a login is (a wrong one
        counts toward this device's lock). Then, in one transaction, the lab's last
        active administrator is refused and every other account is switched
        off with its sessions -- so two administrators leaving at once cannot
        both be the one who was not last.
        """

        account = self.account(account_id)
        if not isinstance(password, str) or not password:
            raise PasswordInvalidError("The password is needed again to delete the account.")
        self.log_in(account.name, password, device=device)
        now = self._stamp(self._now())
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            if account.admin and not self._connection.execute(
                    "SELECT 1 FROM accounts WHERE organization_id=? AND admin=1 AND disabled=0 AND account_id<>?",
                    (account.organization_id, account.account_id)).fetchone():
                raise LastAdminError("This is the lab's last administrator.")
            self._connection.execute(
                "UPDATE accounts SET disabled=1,updated_at=? WHERE account_id=?", (now, account.account_id))
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
        self._connection.execute("PRAGMA secure_delete=ON")
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            counts["sessions"] = self._connection.execute(
                "DELETE FROM sessions WHERE account_id=?", (account.account_id,)).rowcount
            counts["login_attempts"] = self._connection.execute(
                "DELETE FROM login_attempts WHERE name_key=?", (_attempt_key(name_key(account.name)),)).rowcount
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

    def _failure_times(self, name_value: str, device_value: str | None, since: datetime) -> list[datetime]:
        if device_value is None:
            rows = self._connection.execute(
                "SELECT failed_at FROM login_attempts WHERE name_key=? AND failed_at>? ORDER BY failed_at",
                (name_value, self._stamp(since))).fetchall()
        else:
            rows = self._connection.execute(
                "SELECT failed_at FROM login_attempts WHERE name_key=? AND client=? AND failed_at>? ORDER BY failed_at",
                (name_value, device_value, self._stamp(since))).fetchall()
        return [self._parse(row[0]) for row in rows]

    def _check_lock(self, key: str, device: str) -> None:
        """Refuse while this name is locked on this device, or everywhere."""

        now = self._now()
        since = now - LOGIN_FAILURE_WINDOW
        # Failures past the window count for nothing; they are not kept either.
        self._connection.execute("DELETE FROM login_attempts WHERE failed_at<=?", (self._stamp(since),))
        self._connection.commit()
        name_value = _attempt_key(key)
        waits = []
        for limit, device_value in ((LOGIN_FAILURE_LIMIT, device), (LOGIN_NAME_FAILURE_LIMIT, None)):
            times = self._failure_times(name_value, device_value, since)
            if len(times) >= limit:
                # Locked until the failure that keeps the count at the limit leaves the window.
                waits.append(times[len(times) - limit] + LOGIN_FAILURE_WINDOW - now)
        if waits:
            raise LoginLockedError(
                "Too many failed logins; wait.",
                retry_after_seconds=max(1, math.ceil(max(waits).total_seconds())))

    def _record_failure(self, key: str, device: str) -> None:
        now = self._now()
        self._connection.execute(
            "INSERT INTO login_attempts(name_key,client,failed_at) VALUES(?,?,?)",
            (_attempt_key(key), device, self._stamp(now)))
        self._connection.execute(
            "DELETE FROM login_attempts WHERE failed_at<=?", (self._stamp(now - LOGIN_FAILURE_WINDOW),))
        self._connection.commit()

    def log_in(self, name: object, password: object, *, device: object = None) -> Account:
        """The account of a name and password, or one of three refusals.

        ``device`` is the device marker the request carried (None or a
        malformed one: the unknown device); the lock counts by it.
        """

        key = name_key(name)
        device_value = _device_key(device)
        self._check_lock(key, device_value)
        row = self._connection.execute("SELECT * FROM accounts WHERE name_key=?", (key,)).fetchone()
        stored = row["password_hash"] if row is not None else _DUMMY_HASH
        if not verify_password(str(password if password is not None else ""), stored) or row is None:
            self._record_failure(key, device_value)
            raise LoginRefusedError("The name or the password is wrong.")
        account = self._account(row)
        if account.disabled:
            raise AccountDisabledError("The account is switched off.")
        # Only this device's failures are forgiven: another device's guesses still count.
        self._connection.execute(
            "DELETE FROM login_attempts WHERE name_key=? AND client=?", (_attempt_key(key), device_value))
        self._connection.commit()
        return account

    def change_password(
        self, account_id: object, current: object, new: object, *, keep_session_token: str | None = None,
        device: object = None,
    ) -> None:
        """Set a new password after the current one; every other session ends.

        The current password is checked as a login from ``device`` is.
        """

        account = self.account(account_id)
        self.log_in(account.name, current, device=device)
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


# --- Erasing one account's data (lane FX5, decisions 1-2, 2026-10-20) ---------------
#
# ``scripts/pilot_data_delete.py --account NAME`` (the operator, with a preview
# and a confirmation word) and ``POST /api/auth/delete-account`` (the account
# itself, with its password) delete the same things, so the scope is written
# once, here. What goes: the account's experiment sessions with their events,
# completed steps, observations and evidence (rows and files), the experiment
# records bound to it, its pilot metrics rows, its settings, access records and
# ownership rows, and the account with its sessions and login attempts. What the
# lab shares stays -- the protocol list, a review version and the PI
# confirmations left on a version or on another person's notebook -- and the
# name written there becomes ``DELETED_ACCOUNT_LABEL`` (only the name fields:
# words a person typed into a comment or a memo are left as they were). Every
# deleting connection overwrites what it frees (``secure_delete``), and the
# record of a deletion holds categories, counts and times, never a name.

DELETED_ACCOUNT_LABEL = "(삭제된 계정)"
DELETION_LOG_NAME = "deletion_log.jsonl"
#: The stores' names as the deletion record writes them, relative to the pilot folder.
WORKSPACE_ENTRY = "workspace/commercial_workspace.sqlite"
EVIDENCE_ENTRY = "workspace/evidence/"
REPORTS_ENTRY = "experiment_reports.sqlite"
METRICS_ENTRY = "workspace/pilot_metrics.sqlite"
ACCOUNTS_ENTRY = "workspace/accounts.sqlite"
CATALOG_ENTRY = "protocol_workspace.sqlite"
WORKSPACE_DATABASE_FILENAME = "commercial_workspace.sqlite"
REPORTS_DATABASE_FILENAME = "experiment_reports.sqlite"
CATALOG_DATABASE_FILENAME = "protocol_workspace.sqlite"
#: Workspace tables emptied of the account's rows, in an order the foreign
#: keys allow, with the column that names the account or its sessions.
ACCOUNT_WORKSPACE_TABLES = (
    ("experiment_evidence", "session_id"),
    ("experiment_observations", "session_id"),
    ("experiment_completed_steps", "session_id"),
    ("experiment_session_events", "session_id"),
    ("experiment_sessions", "owner_principal_id"),
    ("experimenter_settings", "principal_id"),
    ("admin_audit_events", "actor_principal_id"),
    ("memberships", "principal_id"),
)
ACCOUNT_SHARED_RESOURCE_TYPES = ("experiment_session", "experiment_report")
#: Name fields a review decision, a review version and a PI confirmation carry.
_CATALOG_NAME_FIELDS = frozenset({"reviewer_name", "decided_by"})
_CATALOG_PI_EVENT = "protocol_review_version_pi_confirmed"
_REPORT_PI_EVENT = "pi_confirmed"


@dataclass(frozen=True)
class AccountDataLocations:
    """Where the stores are. ``None`` for a store this deployment does not have."""

    workspace_dir: Path
    reports_db: Path | None = None
    catalog_db: Path | None = None
    log_path: Path | None = None

    @classmethod
    def pilot_folder(cls, data_dir: Path | str) -> "AccountDataLocations":
        """The pilot folder's layout (``scripts/run_pilot.sh``: data/runtime/pilot)."""

        folder = Path(data_dir)
        return cls(
            workspace_dir=folder / "workspace", reports_db=folder / REPORTS_DATABASE_FILENAME,
            catalog_db=folder / CATALOG_DATABASE_FILENAME, log_path=folder / DELETION_LOG_NAME)

    @property
    def accounts_db(self) -> Path:
        return self.workspace_dir / ACCOUNTS_DATABASE_FILENAME

    @property
    def workspace_db(self) -> Path:
        return self.workspace_dir / WORKSPACE_DATABASE_FILENAME

    @property
    def metrics_db(self) -> Path:
        from voiney_lab.pilot_metrics import PILOT_METRICS_FILENAME

        return self.workspace_dir / PILOT_METRICS_FILENAME


@dataclass
class AccountEntry:
    """One store's share of an account's data: row counts, row keys and files."""

    category: str
    label: str
    rows: dict[str, int] = field(default_factory=dict)
    keys: list[str] = field(default_factory=list)
    files: list[Path] = field(default_factory=list)


def _copy_of(path: Path, temp: Path) -> Path:
    copy = temp / path.name
    shutil.copyfile(path, copy)
    wal = Path(str(path) + "-wal")
    if wal.is_file():
        shutil.copyfile(wal, Path(str(copy) + "-wal"))
    return copy


def _open_sqlite(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def _in_clause(values: list[str]) -> tuple[str, tuple[str, ...]]:
    return ("(" + ",".join("?" for _ in values) + ")", tuple(values)) if values else ("(NULL)", ())


def _without_name(value: object, fields: frozenset[str], target: str) -> tuple[object, int]:
    """``value`` with every ``fields`` string that is the account's name replaced; and how many were."""

    if isinstance(value, dict):
        result: dict[str, object] = {}
        changed = 0
        for key, item in value.items():
            if key in fields and isinstance(item, str) and name_key(item) == target:
                result[key] = DELETED_ACCOUNT_LABEL
                changed += 1
            else:
                result[key], more = _without_name(item, fields, target)
                changed += more
        return result, changed
    if isinstance(value, list):
        items = [_without_name(item, fields, target) for item in value]
        return [item for item, _ in items], sum(count for _, count in items)
    return value, 0


def _name_fields(event_type: str, payload_event: str, own: frozenset[str]) -> frozenset[str]:
    return frozenset({"name"}) if event_type == payload_event else own


def _named_rows(
    connection: sqlite3.Connection, table: str, key: str, type_column: str, payload_column: str,
    fields_of: Callable[[str], frozenset[str]], name: str,
) -> list[tuple[object, str]]:
    """(row key, rewritten payload) of each row of ``table`` whose payload holds the name in a name field."""

    found: list[tuple[object, str]] = []
    target = name_key(name)
    for row in connection.execute(
            f"SELECT {key},{type_column},{payload_column} FROM {table} WHERE instr({payload_column},?)>0 ORDER BY {key}",
            (name,)).fetchall():
        try:
            payload = json.loads(row[2])
        except (TypeError, ValueError):
            continue
        rewritten, changed = _without_name(payload, fields_of(str(row[1])), target)
        if changed:
            found.append((row[0], json.dumps(
                rewritten, ensure_ascii=False, sort_keys=True, separators=(",", ":"))))
    return found


def _catalog_fields(event_type: str) -> frozenset[str]:
    return _name_fields(event_type, _CATALOG_PI_EVENT, _CATALOG_NAME_FIELDS)


def _report_fields(event_type: str) -> frozenset[str]:
    return frozenset({"name"}) if event_type == _REPORT_PI_EVENT else frozenset()


def account_data_inventory(
    locations: AccountDataLocations, name: str, *, in_place: bool = False,
) -> tuple[dict[str, object], list[AccountEntry], list[str]]:
    """The account and what it left: (account facts, entries, notes).

    Read from copies unless ``in_place`` (the deletion itself), so a preview
    changes nothing. An unknown name is ``AccountNotFoundError``; a missing
    account store is ``AccountError``.
    """

    from voiney_lab.pilot_metrics import PilotMetricsStore

    if not locations.accounts_db.is_file():
        raise AccountError("The account store is missing.")
    with tempfile.TemporaryDirectory(prefix="account-data-") as temp_name:
        temp = Path(temp_name)

        def source(path: Path) -> Path:
            return path if in_place else _copy_of(path, temp)

        store = AccountStore.open(source(locations.accounts_db))
        try:
            account = store.account_by_name(name)
            if account is None:
                raise AccountNotFoundError("The account is unknown.")
            principal_id = account.principal().principal_id
            facts: dict[str, object] = {
                "account_id": account.account_id, "name": account.name,
                "organization_id": account.organization_id, "principal_id": principal_id,
                "name_key": name_key(account.name),
            }
            accounts_entry = AccountEntry("account_store", "계정·로그인 세션·로그인 시도 (계정 저장소)")
            connection = store._connection  # noqa: SLF001 - the erasure reads the store it will delete from
            for table, column, value in (
                ("sessions", "account_id", account.account_id),
                ("login_attempts", "name_key", _attempt_key(str(facts["name_key"]))),
            ):
                keys = [str(row[0]) for row in connection.execute(
                    f"SELECT rowid FROM {table} WHERE {column}=? ORDER BY rowid", (value,))]
                accounts_entry.rows[table] = len(keys)
                accounts_entry.keys.extend(f"{table}:{key}" for key in keys)
            accounts_entry.rows["accounts"] = 1
            accounts_entry.keys.append(f"accounts:{account.account_id}")
        finally:
            store.close()

        entries: list[AccountEntry] = []
        notes: list[str] = []
        report_ids: list[str] = []
        sessions: list[str] = []
        blank_principal = False
        if locations.workspace_db.is_file():
            connection = _open_sqlite(source(locations.workspace_db))
            try:
                sessions = [str(row[0]) for row in connection.execute(
                    "SELECT session_id FROM experiment_sessions WHERE owner_principal_id=? ORDER BY session_id",
                    (principal_id,))]
                clause, parameters = _in_clause(sessions)
                experiments = AccountEntry("workspace_experiments", "실험 세션·사건·완료 단계·관찰·증거 기록 (작업공간)")
                for table, column in ACCOUNT_WORKSPACE_TABLES[:5]:
                    if column == "session_id":
                        rows = connection.execute(
                            f"SELECT rowid FROM {table} WHERE session_id IN {clause} ORDER BY rowid", parameters).fetchall()
                    else:
                        rows = connection.execute(
                            f"SELECT rowid FROM {table} WHERE {column}=? ORDER BY rowid", (principal_id,)).fetchall()
                    experiments.rows[table] = len(rows)
                    experiments.keys.extend(f"{table}:{row[0]}" for row in rows)
                evidence = AccountEntry("workspace_evidence_files", "증거 파일 (사진·문서)")
                for row in connection.execute(
                        f"SELECT storage_reference FROM experiment_evidence WHERE session_id IN {clause}", parameters):
                    reference = str(row[0])
                    if reference.startswith("/") or ".." in Path(reference).parts:
                        continue
                    file = locations.workspace_dir / reference
                    if file.is_file() and not file.is_symlink():
                        evidence.files.append(file)
                evidence.rows["evidence_files"] = len(evidence.files)
                records = AccountEntry("workspace_account_records", "실험자 설정·접근 기록·자원 소유·구성원·신원 (작업공간)")
                for table, column in ACCOUNT_WORKSPACE_TABLES[5:]:
                    rows = connection.execute(
                        f"SELECT rowid FROM {table} WHERE {column}=? ORDER BY rowid", (principal_id,)).fetchall()
                    records.rows[table] = len(rows)
                    records.keys.extend(f"{table}:{row[0]}" for row in rows)
                bindings = connection.execute(
                    "SELECT rowid,resource_type,resource_id FROM resource_bindings WHERE owner_principal_id=? ORDER BY rowid",
                    (principal_id,)).fetchall()
                owned = [row for row in bindings if row["resource_type"] in ACCOUNT_SHARED_RESOURCE_TYPES]
                shared = [row for row in bindings if row["resource_type"] not in ACCOUNT_SHARED_RESOURCE_TYPES]
                records.rows["resource_bindings"] = len(owned)
                records.keys.extend(f"resource_bindings:{row['rowid']}" for row in owned)
                report_ids = [str(row["resource_id"]) for row in owned if row["resource_type"] == "experiment_report"]
                principal_rows = connection.execute(
                    "SELECT rowid FROM principals WHERE principal_id=?", (principal_id,)).fetchall()
                if principal_rows and not shared:
                    records.rows["principals"] = 1
                    records.keys.append(f"principals:{principal_rows[0][0]}")
                elif principal_rows:
                    records.rows["principals"] = 0
                    blank_principal = True
                    notes.append(f"이 계정이 올린 프로토콜 {len(shared)}건은 연구실 공유라 남기고, "
                                 "신원 행은 이름 없이 남깁니다")
                entries.extend([experiments, evidence, records])
            finally:
                connection.close()
        else:
            notes.append("작업공간 저장소가 없습니다 (실험 세션 없음)")
        if locations.reports_db is not None and locations.reports_db.is_file():
            connection = _open_sqlite(source(locations.reports_db))
            try:
                if report_ids:
                    clause, parameters = _in_clause(report_ids)
                    reports = AccountEntry("experiment_records", "실험 기록 (실험 장부, 보고서 문장)")
                    for table in ("experiment_report_prose", "experiment_report_events", "experiment_reports"):
                        rows = connection.execute(
                            f"SELECT rowid FROM {table} WHERE report_id IN {clause} ORDER BY rowid", parameters).fetchall()
                        reports.rows[table] = len(rows)
                        reports.keys.extend(f"{table}:{row[0]}" for row in rows)
                    entries.append(reports)
                named = _named_rows(
                    connection, "experiment_report_events", "id", "event_type", "payload", _report_fields,
                    account.name)
                # A PI confirmation inside a record that is deleted anyway is not a rename.
                named = [item for item in named if connection.execute(
                    "SELECT report_id FROM experiment_report_events WHERE id=?", (item[0],)).fetchone()[0]
                    not in report_ids]
                if named:
                    entry = AccountEntry(
                        "report_pi_names", "다른 사람 기록에 남긴 PI 확인의 이름 (이름만 바꿈, 실험 기록)")
                    entry.rows["experiment_report_events"] = len(named)
                    entry.keys.extend(f"experiment_report_events:{key}" for key, _ in named)
                    entries.append(entry)
            finally:
                connection.close()
        if locations.catalog_db is not None and locations.catalog_db.is_file():
            connection = _open_sqlite(source(locations.catalog_db))
            try:
                named = _named_rows(
                    connection, "protocol_events", "sequence_id", "event_type", "payload_json", _catalog_fields,
                    account.name)
                if named:
                    entry = AccountEntry(
                        "protocol_catalog_names", "검토 결정·검토본·PI 확인에 적힌 이름 (이름만 바꿈, 프로토콜 카탈로그)")
                    entry.rows["protocol_events"] = len(named)
                    entry.keys.extend(f"protocol_events:{key}" for key, _ in named)
                    entries.append(entry)
            finally:
                connection.close()
        if locations.metrics_db.is_file():
            found = PilotMetricsStore(source(locations.metrics_db)).account_row_ids(principal_id)
            if found:
                metrics = AccountEntry("pilot_metrics", "파일럿 지표 (턴·표시·프로토콜 사건·평점)")
                for table, ids in found.items():
                    metrics.rows[table] = len(ids)
                    metrics.keys.extend(f"{table}:{key}" for key in ids)
                entries.append(metrics)
        entries.append(accounts_entry)
    facts["report_ids"] = report_ids
    facts["sessions"] = sessions
    facts["blank_principal"] = blank_principal
    return facts, entries, notes


def _lift_triggers(connection: sqlite3.Connection, tables: list[str], verb: str) -> list[str]:
    """Drop the append-only ``verb`` ("DELETE" or "UPDATE") triggers of ``tables``; their SQL, to put back."""

    kept: list[str] = []
    for table in tables:
        for row in connection.execute(
                "SELECT name,sql FROM sqlite_master WHERE type='trigger' AND tbl_name=? AND sql LIKE ?",
                (table, f"%BEFORE {verb}%")).fetchall():
            connection.execute(f'DROP TRIGGER "{str(row[0]).replace(chr(34), chr(34) * 2)}"')
            kept.append(str(row[1]))
    return kept


def _failed(record: dict[str, object], exc: Exception) -> None:
    record["result"] = "failed"
    record["error"] = type(exc).__name__


def _rename_rows(
    path: Path, table: str, key: str, column: str, entry: AccountEntry, rewritten: dict[str, str], lift: bool,
) -> None:
    """Write each row's payload with the name replaced; the append-only guard is lifted and put back."""

    connection = _open_sqlite(path)
    try:
        connection.execute("PRAGMA secure_delete=ON")
        connection.execute("BEGIN IMMEDIATE")
        try:
            triggers = _lift_triggers(connection, [table], "UPDATE") if lift else []
            for row_key, payload in rewritten.items():
                connection.execute(f"UPDATE {table} SET {column}=? WHERE {key}=?", (payload, row_key))
            for sql in triggers:
                connection.execute(sql)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    finally:
        connection.close()


def delete_account_data(
    locations: AccountDataLocations, facts: dict[str, object], entries: list[AccountEntry],
) -> list[dict[str, object]]:
    """Delete the rows and files the inventory listed; one record per store, counts only."""

    from voiney_lab.pilot_metrics import PilotMetricsStore

    results: list[dict[str, object]] = []
    by_category = {entry.category: entry for entry in entries}
    principal_id = str(facts["principal_id"])
    if "workspace_experiments" in by_category:
        record: dict[str, object] = {"category": "workspace_records", "entry": WORKSPACE_ENTRY,
                                     "rows": {**by_category["workspace_experiments"].rows,
                                              **by_category["workspace_account_records"].rows}}
        connection = _open_sqlite(locations.workspace_db)
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA secure_delete=ON")
            connection.execute("BEGIN IMMEDIATE")
            try:
                tables = [table for table, _ in ACCOUNT_WORKSPACE_TABLES] + ["resource_bindings"]
                triggers = _lift_triggers(connection, tables, "DELETE")
                clause, parameters = _in_clause(list(facts["sessions"]))  # type: ignore[arg-type]
                for table, column in ACCOUNT_WORKSPACE_TABLES:
                    if column == "session_id":
                        connection.execute(f"DELETE FROM {table} WHERE session_id IN {clause}", parameters)
                    else:
                        connection.execute(f"DELETE FROM {table} WHERE {column}=?", (principal_id,))
                types, type_parameters = _in_clause(list(ACCOUNT_SHARED_RESOURCE_TYPES))
                connection.execute(
                    f"DELETE FROM resource_bindings WHERE owner_principal_id=? AND resource_type IN {types}",
                    (principal_id, *type_parameters))
                blanked = ("UPDATE principals SET display_name=?,subject=? WHERE principal_id=?",
                           (DELETED_ACCOUNT_LABEL,
                            "deleted:" + hashlib.sha256(principal_id.encode("utf-8")).hexdigest()[:16], principal_id))
                if facts["blank_principal"]:
                    connection.execute(*blanked)
                else:
                    # An identity row something else still points at is blanked, not deleted.
                    connection.execute("SAVEPOINT identity_row")
                    try:
                        connection.execute("DELETE FROM principals WHERE principal_id=?", (principal_id,))
                        connection.execute("RELEASE identity_row")
                    except sqlite3.IntegrityError:
                        connection.execute("ROLLBACK TO identity_row")
                        connection.execute("RELEASE identity_row")
                        connection.execute(*blanked)
                for sql in triggers:
                    connection.execute(sql)
                connection.commit()
                record["result"] = "deleted"
            except sqlite3.Error as exc:
                connection.rollback()
                _failed(record, exc)
        finally:
            connection.close()
        results.append(record)
        files = by_category["workspace_evidence_files"].files
        file_record: dict[str, object] = {"category": "workspace_evidence_files", "entry": EVIDENCE_ENTRY,
                                          "files": len(files), "bytes": sum(f.stat().st_size for f in files if f.exists()),
                                          "result": "deleted"}
        for file in files:
            try:
                file.unlink()
                parent = file.parent
                while parent != locations.workspace_dir and parent.is_dir() and not any(parent.iterdir()):
                    parent.rmdir()
                    parent = parent.parent
            except OSError as exc:
                _failed(file_record, exc)
        results.append(file_record)
    if "experiment_records" in by_category and locations.reports_db is not None:
        record = {"category": "experiment_records", "entry": REPORTS_ENTRY, "rows": by_category["experiment_records"].rows}
        connection = _open_sqlite(locations.reports_db)
        try:
            connection.execute("PRAGMA secure_delete=ON")
            connection.execute("BEGIN IMMEDIATE")
            try:
                clause, parameters = _in_clause(list(facts["report_ids"]))  # type: ignore[arg-type]
                for table in ("experiment_report_prose", "experiment_report_events", "experiment_reports"):
                    connection.execute(f"DELETE FROM {table} WHERE report_id IN {clause}", parameters)
                connection.commit()
                record["result"] = "deleted"
            except sqlite3.Error as exc:
                connection.rollback()
                _failed(record, exc)
        finally:
            connection.close()
        results.append(record)
    for category, entry_name, path, table, key, type_column, column, fields_of, lift in (
        ("report_pi_names", REPORTS_ENTRY, locations.reports_db, "experiment_report_events", "id",
         "event_type", "payload", _report_fields, False),
        ("protocol_catalog_names", CATALOG_ENTRY, locations.catalog_db, "protocol_events", "sequence_id",
         "event_type", "payload_json", _catalog_fields, True),
    ):
        if category not in by_category or path is None:
            continue
        record = {"category": category, "entry": entry_name, "rows": by_category[category].rows}
        try:
            wanted = {key_text.split(":", 1)[1] for key_text in by_category[category].keys}
            connection = _open_sqlite(path)
            try:
                found = _named_rows(connection, table, key, type_column, column, fields_of, str(facts["name"]))
            finally:
                connection.close()
            _rename_rows(path, table, key, column, by_category[category],
                         {row_key: payload for row_key, payload in found if str(row_key) in wanted}, lift)
            record["result"] = "deleted"
        except sqlite3.Error as exc:
            _failed(record, exc)
        results.append(record)
    if "pilot_metrics" in by_category:
        record = {"category": "pilot_metrics", "entry": METRICS_ENTRY, "rows": by_category["pilot_metrics"].rows}
        try:
            PilotMetricsStore(locations.metrics_db).delete_account_rows(principal_id)
            record["result"] = "deleted"
        except sqlite3.Error as exc:
            _failed(record, exc)
        results.append(record)
    record = {"category": "account_store", "entry": ACCOUNTS_ENTRY, "rows": by_category["account_store"].rows}
    store = AccountStore.open(locations.accounts_db)
    try:
        store.delete_account(str(facts["account_id"]))
        record["result"] = "deleted"
    except (AccountError, sqlite3.Error) as exc:
        _failed(record, exc)
    finally:
        store.close()
    results.append(record)
    return results


def erase_account_data(locations: AccountDataLocations, account_id: str) -> list[dict[str, object]]:
    """Delete everything of one account from every store (the account deleting itself); the records.

    The account may already be switched off. An unknown id is
    ``AccountNotFoundError`` and nothing is touched.
    """

    store = AccountStore.open(locations.accounts_db)
    try:
        name = store.account(account_id).name
    finally:
        store.close()
    facts, entries, _ = account_data_inventory(locations, name, in_place=True)
    return delete_account_data(locations, facts, entries)


def _overall(results: list[dict[str, object]]) -> str:
    done = [item["result"] == "deleted" for item in results]
    return "deleted" if all(done) else "partial" if any(done) else "failed"


def write_deletion_log(
    path: Path, *, word: str, results: list[dict[str, object]], scope: str = "account", via: str = "tool",
) -> None:
    """Append one record: when, which store, how many -- no file name from the data, no name, no id."""

    record = {
        "deleted_at": _utc_now().isoformat(timespec="seconds"),
        "data_dir": str(Path(path).parent),
        "scope": scope,
        "via": via,
        "confirmation": word,
        "result": _overall(results),
        "categories": results,
    }
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(descriptor, (json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
