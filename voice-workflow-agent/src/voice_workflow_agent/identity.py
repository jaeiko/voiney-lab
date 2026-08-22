"""OIDC-compatible identity resolution and centralized tenant RBAC."""

from __future__ import annotations

import json
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
    RESEARCHER = "researcher"
    REVIEWER = "reviewer"
    LAB_ADMIN = "lab_admin"
    ORGANIZATION_ADMIN = "organization_admin"


class Permission(str, Enum):
    PROTOCOL_READ = "protocol.read"
    PROTOCOL_IMPORT = "protocol.import"
    PROTOCOL_EXECUTE = "protocol.execute"
    PROTOCOL_REVIEW = "protocol.review"
    PROTOCOL_APPROVE = "protocol.approve"
    PROTOCOL_REVOKE = "protocol.revoke"
    REPORT_READ = "report.read"
    REPORT_WRITE = "report.write"
    CONNECTOR_READ = "connector.read"
    CONNECTOR_MANAGE = "connector.manage"
    ELN_WRITEBACK = "eln.writeback"
    KNOWLEDGE_WRITE = "knowledge.write"
    KNOWLEDGE_PROMOTE = "knowledge.promote"
    ASSET_READ = "asset.read"
    ASSET_MANAGE = "asset.manage"
    ANALYTICS_READ = "analytics.read"
    MEMBERSHIP_MANAGE = "membership.manage"
    RETENTION_MANAGE = "retention.manage"


_ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.RESEARCHER: frozenset(
        {
            Permission.PROTOCOL_READ,
            Permission.PROTOCOL_IMPORT,
            Permission.PROTOCOL_EXECUTE,
            Permission.REPORT_READ,
            Permission.REPORT_WRITE,
            Permission.CONNECTOR_READ,
            Permission.ELN_WRITEBACK,
            Permission.KNOWLEDGE_WRITE,
            Permission.ASSET_READ,
        }
    ),
    Role.REVIEWER: frozenset(
        {
            Permission.PROTOCOL_READ,
            Permission.PROTOCOL_IMPORT,
            Permission.PROTOCOL_EXECUTE,
            Permission.PROTOCOL_REVIEW,
            Permission.PROTOCOL_APPROVE,
            Permission.PROTOCOL_REVOKE,
            Permission.REPORT_READ,
            Permission.CONNECTOR_READ,
            Permission.KNOWLEDGE_WRITE,
            Permission.KNOWLEDGE_PROMOTE,
            Permission.ASSET_READ,
        }
    ),
    Role.LAB_ADMIN: frozenset(Permission),
    Role.ORGANIZATION_ADMIN: frozenset(Permission),
}


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


def require_permission(principal: Principal, permission: Permission) -> None:
    if not any(permission in _ROLE_PERMISSIONS[role] for role in principal.roles):
        raise AuthorizationDeniedError("The principal lacks the required role.")


def require_same_tenant(principal: Principal, resource_tenant_id: str) -> None:
    if principal.organization_id != resource_tenant_id:
        raise AuthorizationDeniedError("The resource is not available.")


@dataclass(frozen=True)
class OidcSettings:
    issuer: str
    audience: str
    jwks_url: str
    tenant_claim: str = "organization_id"
    roles_claim: str = "roles"
    display_name_claim: str = "name"

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> OidcSettings | None:
        env = os.environ if environment is None else environment
        values = {
            "issuer": env.get("VOICE_WORKFLOW_AGENT_OIDC_ISSUER", "").strip(),
            "audience": env.get("VOICE_WORKFLOW_AGENT_OIDC_AUDIENCE", "").strip(),
            "jwks_url": env.get("VOICE_WORKFLOW_AGENT_OIDC_JWKS_URL", "").strip(),
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
                "VOICE_WORKFLOW_AGENT_OIDC_TENANT_CLAIM", "organization_id"
            ).strip()
            or "organization_id",
            roles_claim=env.get(
                "VOICE_WORKFLOW_AGENT_OIDC_ROLES_CLAIM", "roles"
            ).strip()
            or "roles",
            display_name_claim=env.get(
                "VOICE_WORKFLOW_AGENT_OIDC_NAME_CLAIM", "name"
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
    raw_roles = claims.get(settings.roles_claim)
    if not isinstance(subject, str) or not isinstance(tenant, str):
        raise AuthenticationRequiredError("Required identity claims are absent.")
    if isinstance(raw_roles, str):
        role_values = (raw_roles,)
    elif isinstance(raw_roles, list) and all(
        isinstance(value, str) for value in raw_roles
    ):
        role_values = tuple(raw_roles)
    else:
        raise AuthorizationDeniedError("No recognized tenant role was supplied.")
    roles = frozenset(Role(value) for value in role_values if value in Role._value2member_map_)
    if not roles:
        raise AuthorizationDeniedError("No recognized tenant role was supplied.")
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
        env = os.environ if environment is None else environment
        raw = env.get("VOICE_WORKFLOW_AGENT_DEV_AUTH_PROFILES", "").strip()
        if not raw:
            profile = DevIdentityProfile(
                profile_id="local-admin",
                principal_id="dev-local-admin",
                organization_id="tenant-local-demo",
                display_name="Local Lab Admin",
                roles=frozenset({Role.LAB_ADMIN}),
            )
            return cls({profile.profile_id: profile})
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise IdentityConfigurationError("Development identity profiles are invalid.") from exc
        if not isinstance(payload, list) or len(payload) > 20:
            raise IdentityConfigurationError("Development identity profiles are invalid.")
        profiles: dict[str, DevIdentityProfile] = {}
        try:
            for item in payload:
                if not isinstance(item, dict):
                    raise ValueError
                profile = DevIdentityProfile(
                    profile_id=str(item["profile_id"]),
                    principal_id=str(item["principal_id"]),
                    organization_id=str(item["organization_id"]),
                    display_name=str(item["display_name"]),
                    roles=frozenset(Role(value) for value in item["roles"]),
                )
                if profile.profile_id in profiles:
                    raise ValueError
                profiles[profile.profile_id] = profile
        except (KeyError, TypeError, ValueError) as exc:
            raise IdentityConfigurationError("Development identity profiles are invalid.") from exc
        return cls(profiles)

    def authenticate(self, profile_id: str | None = None) -> Principal:
        selected = profile_id or "local-admin"
        try:
            return self._profiles[selected].principal()
        except KeyError as exc:
            raise AuthenticationRequiredError(
                "Development identity profile is unavailable."
            ) from exc


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

    def resolve(
        self,
        authorization: str | None,
        *,
        dev_profile_id: str | None = None,
    ) -> Principal:
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
        return self.dev_provider.authenticate(dev_profile_id)
