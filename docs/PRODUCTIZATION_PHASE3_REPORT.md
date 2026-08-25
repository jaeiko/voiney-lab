# Productization Phase 3 — Lab Administrator Experience

Date: 2026-08-24  
Status: Complete  
Product disposition: Controlled Pilot Ready, Field-Unvalidated

## Outcome

The administrator workspace now uses laboratory-operations language and exposes
the effective controls a non-developer manager needs: users, permission levels,
allowed actions, external-connection lifecycle, configuration failures,
authenticated workspace access, privacy-safe usage metrics, and retention.

New external connections no longer become operational when their record is
created. The server enforces a disabled → configuration check → enable sequence,
and the browser never receives credential values or their internal references.

## Implemented changes

### User and permission management

- Replaced developer-facing labels with Account Identifier, User Identity,
  Permission Level, and friendly role names.
- Added the canonical effective-permission list to membership projections and
  shows the allowed work for every active or suspended permission level.
- Retained raw role enums only as internal API values; RBAC enforcement remains
  in the centralized identity module.
- Preserved the server rule that administrators cannot deactivate their own
  administrative permission level.

### Secure connection setup

- Added a visible five-stage flow: select integration, select authentication,
  choose access scope, check configuration, and enable.
- Added tenant-scoped opaque credential handles. The browser can list whether a
  server-provisioned credential is available, but cannot read its environment
  variable, value, or internal `secret://` reference.
- New API-created connectors are always disabled and `untested`; a client cannot
  override that state in the create request.
- Added connector-specific scope-format checks for Google Drive, protocols.io,
  GitHub, and eLabFTW, plus a server credential-resolution check.
- Added a separate enable/disable boundary. Enablement fails closed until the
  configuration check succeeds, including a conditional database update that
  closes a stale-state race.
- Added protocols.io prefix enforcement so its configured protocol scope is now
  applied at import, matching the existing Drive, GitHub, and eLabFTW scope
  enforcement.
- Migrated the workspace database to schema version 6 with connection validation,
  last-check, last-failure, and update-state fields. Legacy enabled connections
  migrate as `untested` and must be checked before operational reuse.

### Security visibility and audit

- Added connection status labels for check required, check failed, ready to
  enable, and enabled, with safe failure reasons and last-check timestamps.
- Added an append-only, tenant-scoped administrative audit ledger for authenticated
  workspace entry, membership changes, connector creation/check/enable/disable,
  and retention-policy changes.
- Added a security overview showing production authentication requirements,
  current authentication mode, connection posture, failures, and analytics
  retention.
- Kept analytics privacy exclusions explicit: raw audio, transcripts, model
  reasoning, credential values, and secrets are not included.
- Made the retention boundary explicit: the configured policy applies to
  privacy-safe analytics events, while access-control activity is a separate
  append-only audit history.

### Responsive operations UI

- Added responsive connection-stage, permission, connection-state, and security
  summary layouts.
- Added desktop/mobile acceptance for product terminology, credential-reference
  non-disclosure, permission visibility, security visibility, and the enforced
  check-before-enable sequence.

## Architecture and authority impact

The change extends the existing identity, workspace-store, workspace-API, and
admin presentation boundaries. It does not change `RequestArbitration`, voice
intent handling, protocol approval authority, experiment completion, or any
client-side workflow checkpoint.

- Role-to-permission expansion comes from the centralized RBAC map.
- Connector lifecycle and audit events are deterministic, tenant-scoped database
  operations using parameterized SQL.
- Credential handles resolve only on the server and never reveal reference or
  credential values in API responses.
- Connection configuration failures disable the connection and remain visible
  without returning provider or secret details.
- Authenticated workspace access writes only a control-plane audit event; it does
  not mutate protocol or experiment state.

## Product evaluation

- Administrator usability: improved. A laboratory manager can understand users,
  allowed work, connection state, next action, retention, and failures without
  knowing OIDC or secret-store terminology.
- Product trust: improved. Creation is not presented as connection success, and
  the UI explicitly distinguishes server configuration checks from live provider
  communication.
- Operational risk: reduced. Connections start disabled, tenant credential
  handles cannot cross organizations, scopes are checked, stale enablement fails,
  and security-relevant changes are append-only.
- Laboratory adoption alignment: improved, but not field-validated. The flow is
  suitable for a controlled administrator pilot exercise, with remaining live
  authentication and provider validation called out below.

## Verification evidence

- Focused identity, workspace, connector, ELN, migration, frontend, and API suite:
  71 tests passed.
- Full Python suite: 786 tests and 691 subtests passed.
- Playwright acceptance matrix: 36 tests passed across Desktop Chrome and Mobile
  Chrome, covering researcher, reviewer, admin, empty/loading, guided connector,
  and responsive flows.
- `python scripts/replay_turns.py`: passed; all seven turns stayed on the curated
  router and no replay turn mutated workflow state.
- `python -m compileall -q src tests scripts`: passed.
- `git diff --check`: passed.

## Remaining risks and deferred work

- The current “configuration check” validates server credential availability and
  access-scope syntax only. It deliberately does not claim provider reachability,
  authentication success, or read access; controlled live-provider validation is
  part of Phase 4.
- Secure credentials must be provisioned by a server operator. Self-service OAuth,
  token refresh, rotation, and revocation workflows are not implemented.
- The access-control audit records authenticated workspace entry and successful or
  failed administrative control actions. It does not persist unattributable
  pre-authentication failures, and its retention lifecycle is not controlled by
  the analytics-retention setting.
- Permission levels use the fixed product RBAC roles; custom roles and per-resource
  grants are not supported.
- Automated responsive acceptance passed, but the deferred screenshot-based
  accessibility/focus review and wet-lab administrator validation remain open.

Phase 3 satisfies its exit gate while keeping external-provider status honest and
all workflow authority on the server. The next sequential phase is pilot
deployment readiness.
