from __future__ import annotations

import pytest

from voiney_lab.identity import (
    AuthenticationRequiredError,
    AuthorizationDeniedError,
    DevIdentityProvider,
    IdentityConfigurationError,
    IdentityResolver,
    OidcSettings,
    Permission,
    Principal,
    Role,
    principal_from_oidc_claims,
    require_permission,
)
from voiney_lab.workspace_store import (
    WorkspaceError,
    WorkspaceNotFoundError,
    WorkspaceSettings,
    initialize_workspace_store,
)


def _principal(
    name: str,
    organization_id: str,
    *roles: Role,
) -> Principal:
    return Principal(
        principal_id=f"principal-{name}",
        subject=f"test:{name}",
        organization_id=organization_id,
        display_name=name,
        roles=frozenset(roles),
        authentication_method="test",
    )


@pytest.fixture
def workspace(tmp_path):
    store = initialize_workspace_store(
        WorkspaceSettings(enabled=True, data_dir=tmp_path, analytics_retention_days=90)
    )
    try:
        yield store
    finally:
        store.close()


def test_rbac_permissions_are_centralized_and_default_deny():
    researcher = _principal("researcher", "tenant-a", Role.RESEARCHER)
    reviewer = _principal("reviewer", "tenant-a", Role.REVIEWER)

    require_permission(researcher, Permission.PROTOCOL_EXECUTE)
    require_permission(reviewer, Permission.PROTOCOL_APPROVE)
    with pytest.raises(AuthorizationDeniedError):
        require_permission(researcher, Permission.PROTOCOL_APPROVE)
    with pytest.raises(AuthorizationDeniedError):
        require_permission(reviewer, Permission.CONNECTOR_MANAGE)


def test_operational_identity_requires_oidc_and_dev_profiles_are_allowlisted():
    with pytest.raises(IdentityConfigurationError):
        IdentityResolver(usage_scope="operational", oidc_settings=None)

    resolver = IdentityResolver(
        usage_scope="development",
        oidc_settings=None,
        dev_provider=DevIdentityProvider.from_environment({}),
    )
    assert resolver.resolve(None).principal_id == "dev-local-admin"
    with pytest.raises(AuthenticationRequiredError):
        resolver.resolve(None, dev_profile_id="client-invented-admin")


def test_oidc_claims_accept_opaque_subject_but_hash_local_identifier():
    settings = OidcSettings(
        issuer="https://identity.example.test",
        audience="voice-workflow",
        jwks_url="https://identity.example.test/.well-known/jwks.json",
    )
    principal = principal_from_oidc_claims(
        {
            "sub": "auth0|user/3f52a+opaque=value",
            "organization_id": "tenant-a",
            "roles": ["researcher", "untrusted-client-role"],
            "name": "Researcher A",
        },
        settings,
    )

    assert principal.subject == "auth0|user/3f52a+opaque=value"
    assert principal.principal_id.startswith("oidc:")
    assert "opaque" not in principal.principal_id
    assert principal.roles == frozenset({Role.RESEARCHER})

    other_issuer = principal_from_oidc_claims(
        {
            "sub": "auth0|user/3f52a+opaque=value",
            "organization_id": "tenant-a",
            "roles": ["researcher"],
        },
        OidcSettings(
            issuer="https://other-identity.example.test",
            audience="voice-workflow",
            jwks_url="https://other-identity.example.test/.well-known/jwks.json",
        ),
    )
    assert other_issuer.principal_id != principal.principal_id


def test_cross_tenant_ids_are_non_enumerable_across_sensitive_resources(workspace):
    tenant_a = _principal("tenant-a-admin", "tenant-a", Role.LAB_ADMIN)
    tenant_b = _principal("tenant-b-admin", "tenant-b", Role.LAB_ADMIN)
    workspace.bootstrap_principal(tenant_a)
    workspace.bootstrap_principal(tenant_b)
    workspace.bind_resource(tenant_a, "report", "report-a")
    workspace.bind_resource(tenant_a, "protocol_catalog", "protocol-a")

    for operation in (
        lambda: workspace.require_resource(tenant_b, "report", "report-a"),
        lambda: workspace.require_resource(tenant_b, "protocol_catalog", "protocol-a"),
    ):
        with pytest.raises(WorkspaceNotFoundError):
            operation()
    assert workspace.resource_ids(tenant_b, "protocol_catalog") == frozenset()
    assert workspace.resource_ids(tenant_a, "protocol_catalog") == frozenset({"protocol-a"})


def test_workspace_settings_require_explicit_absolute_storage(tmp_path):
    with pytest.raises(WorkspaceError, match="absolute"):
        WorkspaceSettings.from_environment(
            {
                "VOINEY_LAB_WORKSPACE_ENABLED": "true",
                "VOINEY_LAB_WORKSPACE_DATA_DIR": "relative/path",
            }
        )
    configured = WorkspaceSettings.from_environment(
        {
            "VOINEY_LAB_WORKSPACE_ENABLED": "true",
            "VOINEY_LAB_WORKSPACE_DATA_DIR": str(tmp_path),
            "VOINEY_LAB_ANALYTICS_RETENTION_DAYS": "30",
        }
    )
    assert configured.analytics_retention_days == 30


def test_asset_location_cards_are_versioned_reviewable_and_tenant_private(workspace):
    admin = _principal("admin", "tenant-a", Role.LAB_ADMIN)
    researcher = _principal("researcher", "tenant-a", Role.RESEARCHER)
    outsider = _principal("outsider", "tenant-b", Role.RESEARCHER)
    for principal in (admin, researcher, outsider):
        workspace.bootstrap_principal(principal)
    first = workspace.add_asset_card_version(
        admin,
        asset_id="trypsin",
        asset_kind="reagent",
        name="Trypsin",
        location={"building": "Science", "room": "302", "storage": "Freezer A", "shelf": "2"},
        barcode="TRY-001",
        sds_url="https://sds.example.test/trypsin",
    )
    second = workspace.add_asset_card_version(
        admin,
        asset_id="trypsin",
        asset_kind="reagent",
        name="Trypsin",
        location={"building": "Science", "room": "302", "storage": "Freezer B", "drawer": "1"},
        barcode="TRY-001",
        sds_url="https://sds.example.test/trypsin",
        review_status="reviewed",
    )
    assert [item["version_id"] for item in workspace.asset_card_history(researcher, "trypsin")] == [first, second]
    difference = workspace.asset_card_diff(researcher, "trypsin")
    assert difference["changes"]["location"]["before"]["storage"] == "Freezer A"
    assert difference["changes"]["location"]["after"]["storage"] == "Freezer B"
    with pytest.raises(WorkspaceNotFoundError):
        workspace.asset_card_history(outsider, "trypsin")
