"""Google OIDC authorization-code flow and invitation-controlled lab membership.

## Status

**Scaffolded and contract-tested. Not live-validated.** No request in this
repository has ever reached Google. The transport and the ID-token verifier are
both injected, the tests drive them with fakes, and there are no credentials
here to make a real call with. Anyone reading a report that upgrades this to
"validated" without a real deployment behind it is being misled.

## Why this module exists

`identity.py` already verifies a *bearer token* that some other system issued,
which is the right seam for a machine-to-machine caller. It is not enough for a
researcher opening a browser: that person needs an interactive sign-in, and the
pilot needs to control which lab they land in.

This module supplies exactly the missing half - build an authorization URL,
validate what comes back, and turn verified claims into a lab membership - and
deliberately supplies nothing else. It issues no roles of its own: every
authorization decision still runs through `identity.py`'s existing RBAC, so
there is one authorization system, not two.

## Membership is by invitation

A pilot must not let anyone with a Google account create or join a lab. A
successful sign-in only proves who someone is; membership is a separate,
pre-existing record an administrator created. A verified identity with no
invitation gets a clear "ask your lab admin" outcome, never a new workspace.

## Development mode

The development sign-in path is unchanged and stays available. It is labelled
개발 모드 in the product and resolves through the *same* membership interface as
this module, so the production path is not a separate universe that only gets
exercised in production.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import secrets
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from urllib.parse import urlencode, urlparse

from voice_workflow_agent.configuration import bounded_integer
from voice_workflow_agent.identity import Role


GOOGLE_AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_ISSUERS = frozenset({"https://accounts.google.com", "accounts.google.com"})

#: Identity only. This flow never requests access to a user's Google data,
#: because nothing in this product reads it.
GOOGLE_SCOPES = ("openid", "email", "profile")

_HOSTED_DOMAIN = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
                            r"(\.[A-Za-z]{2,63})+$")


class GoogleLoginError(RuntimeError):
    code = "google_login_failed"


class GoogleLoginConfigurationError(GoogleLoginError):
    code = "google_login_configuration_invalid"


class GoogleLoginRejected(GoogleLoginError):
    """The sign-in was well-formed but is not allowed to proceed."""

    code = "google_login_rejected"


@dataclass(frozen=True)
class GoogleLoginSettings:
    """Configuration for interactive Google sign-in.

    The client secret is read from the environment at request time and never
    stored on this object, so it cannot reach a log line, a repr, or a crash
    dump through here.
    """

    enabled: bool = False
    client_id: str = ""
    redirect_uri: str = ""
    #: Restrict sign-in to one or more Google Workspace domains. Empty means no
    #: domain restriction, which is only appropriate when membership is
    #: invitation-controlled - as it is here.
    allowed_hosted_domains: tuple[str, ...] = ()
    #: Off means a verified identity without an existing invitation is refused.
    #: It stays off for the pilot.
    allow_self_service_membership: bool = False
    #: Bounds how long an authorization request may sit unfinished.
    challenge_lifetime_seconds: int = 600
    session_lifetime_seconds: int = 43200
    #: Set false only for local HTTP development. Production must keep it true.
    require_secure_cookie: bool = True

    #: Name of the environment variable holding the client secret. The value is
    #: never captured here.
    client_secret_environment_variable: str = "GOOGLE_OIDC_CLIENT_SECRET"

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None,
    ) -> "GoogleLoginSettings":
        env = os.environ if environment is None else environment
        enabled = bounded_integer(
            env, "VOICE_WORKFLOW_AGENT_GOOGLE_LOGIN_ENABLED", 0, 0, 1) == 1
        domains = tuple(
            item.strip().lower()
            for item in env.get(
                "VOICE_WORKFLOW_AGENT_GOOGLE_ALLOWED_DOMAINS", "").split(",")
            if item.strip()
        )
        settings = cls(
            enabled=enabled,
            client_id=env.get("GOOGLE_OIDC_CLIENT_ID", "").strip(),
            redirect_uri=env.get("GOOGLE_OIDC_REDIRECT_URI", "").strip(),
            allowed_hosted_domains=domains,
            allow_self_service_membership=bounded_integer(
                env, "VOICE_WORKFLOW_AGENT_GOOGLE_SELF_SERVICE_LAB", 0, 0, 1
            ) == 1,
            challenge_lifetime_seconds=bounded_integer(
                env, "VOICE_WORKFLOW_AGENT_GOOGLE_CHALLENGE_TTL", 600, 60, 3600),
            session_lifetime_seconds=bounded_integer(
                env, "VOICE_WORKFLOW_AGENT_SESSION_TTL", 43200, 300, 604800),
            require_secure_cookie=bounded_integer(
                env, "VOICE_WORKFLOW_AGENT_REQUIRE_SECURE_COOKIE", 1, 0, 1) == 1,
        )
        if enabled:
            settings.validate()
        return settings

    def validate(self) -> None:
        """Fail loudly at configuration time rather than at a user's first click."""

        if not self.client_id:
            raise GoogleLoginConfigurationError(
                "GOOGLE_OIDC_CLIENT_ID is required when Google login is enabled.")
        if not self.redirect_uri:
            raise GoogleLoginConfigurationError(
                "GOOGLE_OIDC_REDIRECT_URI is required when Google login is "
                "enabled.")
        parsed = urlparse(self.redirect_uri)
        if parsed.scheme != "https" and parsed.hostname not in {
            "localhost", "127.0.0.1",
        }:
            raise GoogleLoginConfigurationError(
                "GOOGLE_OIDC_REDIRECT_URI must use HTTPS outside localhost.")
        if parsed.scheme != "https" and self.require_secure_cookie:
            raise GoogleLoginConfigurationError(
                "A non-HTTPS redirect URI cannot be combined with a Secure "
                "session cookie. Fix the URI, or disable the requirement only "
                "for local development.")
        for domain in self.allowed_hosted_domains:
            if not _HOSTED_DOMAIN.fullmatch(domain):
                raise GoogleLoginConfigurationError(
                    f"Allowed Google domain is malformed: {domain}")

    def client_secret(
        self, environment: Mapping[str, str] | None = None,
    ) -> str:
        env = os.environ if environment is None else environment
        secret = env.get(self.client_secret_environment_variable, "").strip()
        if not secret:
            raise GoogleLoginConfigurationError(
                f"{self.client_secret_environment_variable} is not configured.")
        return secret


def _url_safe(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


@dataclass(frozen=True)
class LoginChallenge:
    """One in-flight authorization request.

    ``state`` and ``nonce`` are single-use and must be stored server-side, not
    in a URL the browser can be talked into replaying. ``code_verifier`` is the
    PKCE secret and never leaves the server.
    """

    state: str
    nonce: str
    code_verifier: str
    authorization_url: str
    created_at: float

    @property
    def code_challenge(self) -> str:
        return _url_safe(hashlib.sha256(self.code_verifier.encode("ascii")).digest())

    def expired(self, now: float, settings: GoogleLoginSettings) -> bool:
        return now - self.created_at > settings.challenge_lifetime_seconds


def begin_login(
    settings: GoogleLoginSettings,
    *,
    now: float,
    login_hint: str | None = None,
) -> LoginChallenge:
    """Build the authorization URL with state, nonce and PKCE S256."""

    if not settings.enabled:
        raise GoogleLoginConfigurationError("Google login is not enabled.")
    settings.validate()
    state = _url_safe(secrets.token_bytes(32))
    nonce = _url_safe(secrets.token_bytes(32))
    verifier = _url_safe(secrets.token_bytes(64))
    challenge = _url_safe(
        hashlib.sha256(verifier.encode("ascii")).digest())
    parameters = {
        "client_id": settings.client_id,
        "redirect_uri": settings.redirect_uri,
        "response_type": "code",
        "scope": " ".join(GOOGLE_SCOPES),
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        # Ask for a fresh account choice rather than silently reusing whichever
        # Google account the browser happens to be signed into.
        "prompt": "select_account",
    }
    if settings.allowed_hosted_domains:
        parameters["hd"] = settings.allowed_hosted_domains[0]
    if login_hint:
        parameters["login_hint"] = login_hint
    return LoginChallenge(
        state=state, nonce=nonce, code_verifier=verifier,
        authorization_url=(
            f"{GOOGLE_AUTHORIZATION_ENDPOINT}?{urlencode(parameters)}"),
        created_at=now,
    )


#: Exchanges an authorization code for a token response. Injected so the flow is
#: testable without a network, and so the HTTP client stays one concern.
TokenExchange = Callable[[str, Mapping[str, str]], Mapping[str, object]]

#: Verifies a Google ID token and returns its claims. Injected for the same
#: reason. A real implementation must check the signature against Google's JWKS,
#: the issuer, the audience and the expiry - this module then checks the rest.
IdTokenVerifier = Callable[[str], Mapping[str, object]]


@dataclass(frozen=True)
class VerifiedGoogleIdentity:
    """A person Google vouched for. Not yet a member of anything."""

    subject: str
    email: str
    email_verified: bool
    display_name: str
    hosted_domain: str | None = None


def complete_login(
    settings: GoogleLoginSettings,
    challenge: LoginChallenge,
    *,
    returned_state: str,
    code: str,
    now: float,
    exchange: TokenExchange,
    verify_id_token: IdTokenVerifier,
    environment: Mapping[str, str] | None = None,
) -> VerifiedGoogleIdentity:
    """Validate a callback and return the identity Google vouched for.

    Every check here is a real attack surface, so each rejection is separate and
    explicit rather than folded into one boolean:

    * ``state`` mismatch or a replayed challenge is CSRF;
    * an expired challenge is a stale or captured link;
    * a ``nonce`` mismatch is an ID token minted for a different request;
    * an unverified email is an identity Google itself will not stand behind;
    * a hosted domain outside the allow-list is the wrong organisation.
    """

    if not settings.enabled:
        raise GoogleLoginConfigurationError("Google login is not enabled.")
    if not returned_state or not secrets.compare_digest(
        str(returned_state), challenge.state,
    ):
        raise GoogleLoginRejected("Authorization state did not match.")
    if challenge.expired(now, settings):
        raise GoogleLoginRejected("Authorization request expired.")
    if not code or not isinstance(code, str):
        raise GoogleLoginRejected("Authorization code is missing.")

    payload = exchange(GOOGLE_TOKEN_ENDPOINT, {
        "code": code,
        "client_id": settings.client_id,
        "client_secret": settings.client_secret(environment),
        "redirect_uri": settings.redirect_uri,
        "grant_type": "authorization_code",
        "code_verifier": challenge.code_verifier,
    })
    id_token = payload.get("id_token") if isinstance(payload, Mapping) else None
    if not isinstance(id_token, str) or not id_token:
        raise GoogleLoginRejected("Token response carried no ID token.")

    claims = verify_id_token(id_token)
    if not isinstance(claims, Mapping):
        raise GoogleLoginRejected("ID token claims are malformed.")
    if str(claims.get("iss", "")) not in GOOGLE_ISSUERS:
        raise GoogleLoginRejected("ID token issuer is not Google.")
    if str(claims.get("aud", "")) != settings.client_id:
        raise GoogleLoginRejected("ID token audience is not this application.")
    if not secrets.compare_digest(str(claims.get("nonce", "")), challenge.nonce):
        raise GoogleLoginRejected("ID token nonce did not match.")

    subject = str(claims.get("sub", "")).strip()
    email = str(claims.get("email", "")).strip().lower()
    if not subject or not email:
        raise GoogleLoginRejected("ID token is missing subject or email.")
    if claims.get("email_verified") is not True:
        raise GoogleLoginRejected("Google has not verified this email address.")

    hosted = claims.get("hd")
    hosted_domain = str(hosted).strip().lower() if isinstance(hosted, str) else None
    if settings.allowed_hosted_domains:
        if hosted_domain not in settings.allowed_hosted_domains:
            raise GoogleLoginRejected(
                "This Google account is outside the allowed organisation.")

    display = str(claims.get("name", "")).strip() or email.split("@", 1)[0]
    return VerifiedGoogleIdentity(
        subject=subject, email=email, email_verified=True,
        display_name=display[:120], hosted_domain=hosted_domain,
    )


@dataclass(frozen=True)
class LabInvitation:
    """A membership an administrator created before the person ever signed in."""

    email: str
    lab_id: str
    role: Role = Role.RESEARCHER
    display_name: str = ""

    def __post_init__(self) -> None:
        if "@" not in self.email:
            raise ValueError("invitation email is invalid")
        if not self.lab_id.strip():
            raise ValueError("invitation lab_id is required")


@dataclass(frozen=True)
class LabMembershipResolution:
    """What a verified identity is allowed to enter, and with what role."""

    granted: bool
    lab_id: str | None = None
    role: Role | None = None
    reason: str = ""
    #: Korean guidance for a first-time user who has no invitation yet.
    message: str | None = None


NO_INVITATION_MESSAGE = (
    "로그인은 확인됐지만 아직 참여 중인 연구실이 없습니다. "
    "연구실 관리자에게 초대를 요청해 주세요."
)


def resolve_lab_membership(
    identity: VerifiedGoogleIdentity,
    invitations: Sequence[LabInvitation],
    settings: GoogleLoginSettings,
    *,
    requested_lab_id: str | None = None,
) -> LabMembershipResolution:
    """Turn a verified identity into a lab membership, or refuse it.

    Signing in is not joining. For the pilot the only route into a lab is an
    invitation an administrator created, so a stranger with a valid Google
    account gets a polite dead end rather than a new workspace.
    """

    matches = [
        invitation for invitation in invitations
        if invitation.email.strip().lower() == identity.email
    ]
    if not matches:
        if settings.allow_self_service_membership:
            return LabMembershipResolution(
                granted=False, reason="self_service_not_implemented",
                message=NO_INVITATION_MESSAGE)
        return LabMembershipResolution(
            granted=False, reason="no_invitation",
            message=NO_INVITATION_MESSAGE)
    if requested_lab_id:
        chosen = next(
            (item for item in matches if item.lab_id == requested_lab_id), None)
        if chosen is None:
            return LabMembershipResolution(
                granted=False, reason="lab_not_authorized",
                message=NO_INVITATION_MESSAGE)
    else:
        chosen = matches[0]
    return LabMembershipResolution(
        granted=True, lab_id=chosen.lab_id, role=chosen.role,
        reason="invited_member")


@dataclass(frozen=True)
class SessionCookie:
    """Attributes for the browser session cookie this flow would issue."""

    name: str
    value: str
    max_age: int
    http_only: bool = True
    same_site: str = "Lax"
    secure: bool = True
    path: str = "/"

    def header_value(self) -> str:
        parts = [
            f"{self.name}={self.value}",
            f"Path={self.path}",
            f"Max-Age={self.max_age}",
            f"SameSite={self.same_site}",
        ]
        if self.http_only:
            parts.append("HttpOnly")
        if self.secure:
            parts.append("Secure")
        return "; ".join(parts)


SESSION_COOKIE_NAME = "vwa_session"


def issue_session_cookie(
    settings: GoogleLoginSettings, *, session_token: str,
) -> SessionCookie:
    """Describe the cookie a completed sign-in would set.

    HttpOnly so page scripts cannot read it, SameSite=Lax so a cross-site form
    post cannot ride it while an ordinary top-level navigation still works, and
    Secure whenever the deployment is not local HTTP. The provider's own tokens
    stay on the server: nothing from Google is handed to the browser, and
    nothing goes into localStorage, where any injected script could read it.
    """

    if not session_token or len(session_token) < 32:
        raise GoogleLoginConfigurationError(
            "Session token must be at least 32 characters of entropy.")
    return SessionCookie(
        name=SESSION_COOKIE_NAME,
        value=session_token,
        max_age=settings.session_lifetime_seconds,
        http_only=True,
        same_site="Lax",
        secure=settings.require_secure_cookie,
    )


def new_session_token() -> str:
    return _url_safe(secrets.token_bytes(32))


@dataclass
class ChallengeStore:
    """Server-side, single-use storage for in-flight authorization requests.

    In-memory and process-local on purpose: this is a scaffold, and a shared
    deployment needs a shared store. Recording that here is more useful than
    pretending an in-memory dictionary would survive two workers.
    """

    challenges: dict[str, LoginChallenge] = field(default_factory=dict)

    def remember(self, challenge: LoginChallenge) -> None:
        self.challenges[challenge.state] = challenge

    def take(self, state: str) -> LoginChallenge | None:
        """Consume a challenge. A second callback with the same state fails."""

        return self.challenges.pop(str(state or ""), None)

    def purge_expired(self, now: float, settings: GoogleLoginSettings) -> int:
        stale = [
            state for state, challenge in self.challenges.items()
            if challenge.expired(now, settings)
        ]
        for state in stale:
            self.challenges.pop(state, None)
        return len(stale)


#: Every environment variable this flow needs, for the deployment runbook.
REQUIRED_ENVIRONMENT = (
    ("VOICE_WORKFLOW_AGENT_GOOGLE_LOGIN_ENABLED", "1 to enable interactive sign-in"),
    ("GOOGLE_OIDC_CLIENT_ID", "OAuth 2.0 Web application client ID"),
    ("GOOGLE_OIDC_CLIENT_SECRET", "client secret; never committed"),
    ("GOOGLE_OIDC_REDIRECT_URI", "HTTPS callback registered with Google"),
    ("VOICE_WORKFLOW_AGENT_GOOGLE_ALLOWED_DOMAINS",
     "optional comma-separated Workspace domains"),
    ("VOICE_WORKFLOW_AGENT_REQUIRE_SECURE_COOKIE",
     "1 in production; 0 only for local HTTP development"),
)
