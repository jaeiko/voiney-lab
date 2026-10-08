"""OIDC-compatible identity resolution for the one identity the MVP knows: the experimenter."""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

import jwt
from jwt import PyJWKClient


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,159}$")


class IdentityError(RuntimeError):
    code = "identity_invalid"


class AuthenticationRequiredError(IdentityError):
    code = "authentication_required"


class AuthorizationDeniedError(IdentityError):
    code = "authorization_denied"


class IdentityConfigurationError(IdentityError):
    code = "identity_configuration_invalid"


class Role(str, Enum):
    """The one identity the MVP knows: the experimenter at the bench.

    Lane DI (2026-10-08, decision 4): the reviewer and lab-admin roles went
    with their screens. The value keeps the memberships table's vocabulary
    ('researcher'), so rows written before this change still count as a
    membership; the stored role text is no longer read as a permission.
    """

    RESEARCHER = "researcher"


@dataclass(frozen=True)
class Principal:
    principal_id: str
    subject: str
    organization_id: str
    display_name: str
    roles: frozenset[Role]
    authentication_method: str

    def __post_init__(self) -> None:
        for value in (self.principal_id, self.organization_id):
            if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
                raise IdentityError("Principal identity is malformed.")
        if (
            not isinstance(self.subject, str)
            or not self.subject
            or len(self.subject) > 512
            or any(ord(character) < 32 for character in self.subject)
        ):
            raise IdentityError("Principal subject is malformed.")
        if not self.roles:
            raise IdentityError("Principal has no active role.")


def require_same_tenant(principal: Principal, resource_tenant_id: str) -> None:
    if principal.organization_id != resource_tenant_id:
        raise AuthorizationDeniedError("The resource is not available.")


@dataclass(frozen=True)
class OidcSettings:
    issuer: str
    audience: str
    jwks_url: str
    tenant_claim: str = "organization_id"
    display_name_claim: str = "name"

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> OidcSettings | None:
        env = os.environ if environment is None else environment
        values = {
            "issuer": env.get("VOINEY_LAB_OIDC_ISSUER", "").strip(),
            "audience": env.get("VOINEY_LAB_OIDC_AUDIENCE", "").strip(),
            "jwks_url": env.get("VOINEY_LAB_OIDC_JWKS_URL", "").strip(),
        }
        if not any(values.values()):
            return None
        if not all(values.values()):
            raise IdentityConfigurationError(
                "OIDC issuer, audience, and JWKS URL must be configured together."
            )
        if not values["issuer"].startswith("https://") or not values[
            "jwks_url"
        ].startswith("https://"):
            raise IdentityConfigurationError("OIDC metadata must use HTTPS.")
        return cls(
            **values,
            tenant_claim=env.get(
                "VOINEY_LAB_OIDC_TENANT_CLAIM", "organization_id"
            ).strip()
            or "organization_id",
            display_name_claim=env.get(
                "VOINEY_LAB_OIDC_NAME_CLAIM", "name"
            ).strip()
            or "name",
        )


class BearerTokenVerifier(Protocol):
    def verify(self, token: str) -> Mapping[str, object]: ...


class OidcJwtVerifier:
    """Verify signed ID/access-token claims against a configured issuer/JWKS."""

    def __init__(
        self,
        settings: OidcSettings,
        *,
        jwk_client: PyJWKClient | None = None,
    ) -> None:
        self.settings = settings
        self._jwk_client = jwk_client or PyJWKClient(
            settings.jwks_url,
            cache_jwk_set=True,
            lifespan=300,
        )

    def verify(self, token: str) -> Mapping[str, object]:
        if not isinstance(token, str) or not token or len(token) > 16_384:
            raise AuthenticationRequiredError("Bearer token is invalid.")
        try:
            key = self._jwk_client.get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256", "ES256"],
                audience=self.settings.audience,
                issuer=self.settings.issuer,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise AuthenticationRequiredError("Bearer token verification failed.") from exc
        if not isinstance(claims, dict):
            raise AuthenticationRequiredError("Bearer claims are invalid.")
        return claims


def principal_from_oidc_claims(
    claims: Mapping[str, object], settings: OidcSettings
) -> Principal:
    subject = claims.get("sub")
    tenant = claims.get(settings.tenant_claim)
    if not isinstance(subject, str) or not isinstance(tenant, str):
        raise AuthenticationRequiredError("Required identity claims are absent.")
    # Lane DI (2026-10-08): every authenticated person is the experimenter;
    # a roles claim, if the issuer sends one, is not read.
    roles = frozenset({Role.RESEARCHER})
    display = claims.get(settings.display_name_claim)
    if len(subject) > 512:
        raise AuthenticationRequiredError("OIDC subject is too long.")
    # ``sub`` is unique only within one issuer.  Keep the externally visible
    # identifier opaque while preventing collisions across identity providers.
    scoped_subject = f"{settings.issuer}|{subject}"
    principal_id = (
        f"oidc:{hashlib.sha256(scoped_subject.encode('utf-8')).hexdigest()[:40]}"
    )
    return Principal(
        principal_id=principal_id,
        subject=subject,
        organization_id=tenant,
        display_name=display if isinstance(display, str) and display else subject,
        roles=roles,
        authentication_method="oidc",
    )


@dataclass(frozen=True)
class DevIdentityProfile:
    profile_id: str
    principal_id: str
    organization_id: str
    display_name: str
    roles: frozenset[Role]

    def principal(self) -> Principal:
        return Principal(
            principal_id=self.principal_id,
            subject=f"dev:{self.profile_id}",
            organization_id=self.organization_id,
            display_name=self.display_name,
            roles=self.roles,
            authentication_method="development",
        )


class DevIdentityProvider:
    """Server-configured profiles; a client can select only an allowlisted ID."""

    def __init__(self, profiles: Mapping[str, DevIdentityProfile]) -> None:
        self._profiles = dict(profiles)

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> DevIdentityProvider:
        """The one development identity (lane DI, 2026-10-08, decision 4).

        Until a login exists, every request in a non-operational scope is
        the experimenter. The ids keep their earlier values so experiments
        recorded under them stay the same person's; only the shown name
        changed. The environment is accepted and ignored: a client-chosen
        profile (header or query string) used to pick the identity, and
        that path is gone.
        """

        del environment
        profile = DevIdentityProfile(
            profile_id="local-admin",
            principal_id="dev-local-admin",
            organization_id="tenant-local-demo",
            display_name="실험자 (개발 로그인)",
            roles=frozenset({Role.RESEARCHER}),
        )
        return cls({profile.profile_id: profile})

    def authenticate(self) -> Principal:
        return next(iter(self._profiles.values())).principal()


class IdentityResolver:
    def __init__(
        self,
        *,
        usage_scope: str,
        oidc_settings: OidcSettings | None,
        oidc_verifier: BearerTokenVerifier | None = None,
        dev_provider: DevIdentityProvider | None = None,
    ) -> None:
        self.usage_scope = usage_scope
        self.oidc_settings = oidc_settings
        self.oidc_verifier = oidc_verifier
        self.dev_provider = dev_provider
        if usage_scope == "operational" and oidc_settings is None:
            raise IdentityConfigurationError("Operational scope requires OIDC.")

    def resolve(self, authorization: str | None) -> Principal:
        if authorization:
            scheme, separator, token = authorization.partition(" ")
            if separator != " " or scheme.casefold() != "bearer" or not token:
                raise AuthenticationRequiredError("Authorization header is invalid.")
            if self.oidc_settings is None:
                raise AuthenticationRequiredError("OIDC is not configured.")
            verifier = self.oidc_verifier or OidcJwtVerifier(self.oidc_settings)
            return principal_from_oidc_claims(
                verifier.verify(token), self.oidc_settings
            )
        if self.usage_scope == "operational":
            raise AuthenticationRequiredError("Authentication is required.")
        if self.dev_provider is None:
            raise AuthenticationRequiredError("Development authentication is disabled.")
        return self.dev_provider.authenticate()
