# Laboratory Workflow OS Implementation Report

Branch: `refactor/voice-workflow-agent-stability`

Starting revision: `071669702767b0441ad3d9e2dd2836318a31494f`

Baseline: `734 passed, 683 subtests passed in 128.96s`

The implementation follows the dependency order in the Laboratory Workflow OS
brief. A phase advances only after focused and full regression coverage,
migration verification, compilation, and documentation.

## Phase 1 — Experiment Session Foundation

Status: implemented; final full-suite result is recorded in the phase validation
ledger below.

### Laboratory pain point

A researcher can lose a browser connection, move benches, or return after an
interruption. A transcript-only assistant cannot prove which exact SOP revision
was running, which step was current, or whether prior steps were completed. The
durable session reduces repeated setup and prevents informal, memory-based
continuation.

### Implementation

- Added tenant-owned `ExperimentSession` persistence with exact protocol and
  revision identity, owner, status, current step, optimistic version, timestamps,
  last voice connection, completed steps, and append-only lifecycle events.
- Added explicit states for `ready`, `in_progress`, `paused`, `blocked`,
  `completed`, and `stopped`. Configuring the microphone produces `ready`; only
  the existing deterministic START intent produces `in_progress`.
- Added pause/resume/stop transitions and exact-revision WebSocket recovery.
- Added contiguous-step validation when restoring the existing
  `CuratedProtocolSession`. Recovery cannot skip an incomplete step and never
  restores pending confirmations, conversational context, model output, or an
  active timer.
- Added researcher-owned dashboard APIs for listing and reading sessions. A
  reviewer/admin may read tenant sessions; a researcher cannot enumerate a
  colleague's or another tenant's sessions.
- Kept `ListenerSession`, VAD/barge-in, turn/generation identity, intent
  arbitration, `CuratedProtocolSession`, and report persistence in place. The
  durable record extends these authorities; it does not replace them.

### Safety and migration evidence

- A schema-v1 workspace is migrated transactionally to schema v2. Existing
  organizations and all v1 tables/rows remain intact.
- The v1 metadata table had a hard-coded `CHECK(schema_version=1)`; migration
  replaces only that metadata table inside the same transaction rather than
  attempting an impossible update.
- Dashboard APIs deliberately cannot mark an experiment complete. Completion
  remains owned by the deterministic protocol path.
- Disconnect does not imply stop or completion. The experiment remains
  recoverable with its current optimistic version.

## Phase 2 — Observation and Evidence Layer

Status: implemented; final full-suite result is recorded in the phase validation
ledger below.

### Laboratory pain point

Bench observations are often captured after the fact in a notebook, detached
from the exact SOP step and without a durable author/time identity. Images may
also be scattered across devices. This causes memory-dependent reconstruction,
weak deviation context, and extra reviewer follow-up. The timeline lets a
researcher capture wording and opaque evidence at the moment of work while
keeping those records separate from approved instructions.

### Implementation

- Added append-only observation entities with experiment session, exact current
  or completed protocol step, author, content, category, voice/manual capture
  source, timestamp, and a fixed `observation_only` knowledge effect.
- Added append-only image/document evidence metadata with uploader, original
  filename, media type, bounded size, SHA-256, storage reference, and a fixed
  `not_interpreted` status. Bytes are streamed to content-addressed tenant
  storage and internal paths are omitted from public responses.
- Added one unified tenant-scoped timeline projection for lifecycle, protocol
  progress, observations, attachments, and reviewer actions.
- Added manual observation, evidence upload, timeline read, and reviewer action
  APIs plus a researcher bench timeline with session selection, manual capture,
  and evidence upload.
- Added deterministic Korean/English voice capture for explicit notes and a
  one-turn “record observation” prompt. “The sample looks different” is an
  appearance observation; a spill remains an anomaly. Neither observation path
  changes the protocol step or approved knowledge.
- Bound voice acknowledgement to a successful durable observation write. A
  failed write cannot be announced as recorded; an observation accompanying a
  completion cannot authorize a step transition if the timeline write fails.
- Kept the existing Cascade WebSocket, VAD/barge-in, turn/generation fences,
  intent arbitration, `CuratedProtocolSession`, source-defined completion gates,
  and report path in place.

### Safety and migration evidence

- Schema 2 → 3 is forward-only and transactional. A schema-2 fixture preserves
  its existing experiment session and exact revision while adding observation
  and evidence tables and append-only triggers.
- Reused idempotency keys must point to the same event and identical content;
  they cannot overwrite a lifecycle event or silently create an orphan record.
- Evidence uploads allow JPEG, PNG, WebP, PDF, and DOCX only, enforce a 32 MiB
  streaming cap, verify an existing content-addressed file before reuse, and run
  no OCR or autonomous interpretation.
- Timeline reads remain owner/tenant scoped. Reviewer actions require the
  existing protocol-review permission; reviewers still cannot alter SOP
  instructions through this path.

## Phase 3 — Experiment Timeline

Status: implemented with the Phase 2 persistence change; final full-suite result
is recorded in the phase validation ledger below.

### Laboratory pain point

Lifecycle, step progress, notes, attachments, and review decisions are difficult
to reconstruct when they live in separate screens or exports. The unified,
chronological timeline gives the researcher and reviewer one authoritative
experiment history without treating chat text as workflow state.

### Implementation and safety evidence

- Added one tenant- and owner-scoped projection over append-only session events,
  resolving observation and evidence detail without exposing internal storage
  paths.
- The researcher bench workspace uses the experiment timeline as its primary
  history view and refreshes it from canonical API/WebSocket state.
- Protocol start, completion, pause/resume, observations, evidence, and reviewer
  actions retain their original actor, step, and timestamp identity.
- Timeline reads are read-only. Observation text and attachments cannot mutate
  protocol instructions, pass completion gates, or become approved knowledge.

## Phase 4 — Lab Adaptation System

Status: implemented; final full-suite result is recorded in the phase validation
ledger below.

### Laboratory pain point

Published protocols commonly name equipment, reagent identities, and practical
details that differ from a lab's qualified local setup. Informal edits in copied
documents destroy source lineage and make it unclear whether a reviewer approved
the change. A typed adaptation draft preserves the original while making every
local difference reviewable.

### Implementation

- Added immutable `protocol_adaptation_revisions` linking an exact original
  lineage revision to an exact adapted child revision and typed change set.
- Added four bounded change types: equipment difference, reagent substitution,
  lab note, and troubleshooting tip. Every item is step-linked and requires a
  summary and rationale; equipment/reagent changes also require distinct
  original and adapted values.
- Adaptation creation copies the immutable base content into a new lineage
  revision, adds explicit review-required adaptation metadata, and inserts the
  normal reviewer inbox item in the same database transaction.
- Reused the existing source diff, reviewer decision, approval history,
  revocation, and executable-state machinery. No second approval authority was
  introduced.
- Added tenant-scoped create/list/read APIs. Reviewers approve the adapted
  revision through the existing reviewer decision endpoint.

### Safety and migration evidence

- The original revision is never updated or deleted. Both lineage and
  adaptation tables are protected by append-only/immutable triggers.
- Rejected/revoked revisions and stale parents cannot be adapted. An adaptation
  cannot be nested on another adaptation; authors must return to the original
  source lineage.
- A development-status source remains impossible to approve directly. Its
  explicit adaptation child may become executable only after reviewer/admin
  approval.
- Schema 3 → 4 is transactional. A schema-3 fixture preserves the original
  lineage content while adding the immutable adaptation relationship.

## Phase 5 — Source Ecosystem Integration

Status: validated as an existing production boundary; final full-suite result is
recorded in the phase validation ledger below.

### Laboratory pain point

Labs already govern protocol files in Drive, protocols.io, and GitHub. Manual
download/re-upload breaks version identity and makes it difficult to prove which
source changed. The source hub turns an allowlisted upstream version into an
immutable, review-required lineage revision without making the voice service a
second uncontrolled document store.

### Validated implementation

- `ProtocolsIoConnector` accepts bounded protocols.io identities, authenticates
  with a server-resolved token, preserves structured source metadata, and never
  promotes an in-development source to approved.
- `GoogleDriveConnector` uses read-only folder/shared-drive allowlists, preserves
  file/revision/modified identity, and advances a persisted changes cursor. A
  changed file creates a new review item instead of overwriting an active SOP.
- `GitHubConnector` uses repository/ref/path allowlists, pins imports to a commit
  SHA, validates webhook HMAC and delivery replay, and never executes imported
  repository content.
- Connector rows contain only secret references and authorization metadata.
  OAuth/App tokens are resolved at the server boundary and are excluded from
  SQLite, API projections, logs, and browser state.
- All three import paths reuse the existing tenant-scoped source, immutable
  lineage revision, reviewer inbox, approval, and revocation machinery.

### Safety and migration evidence

- Connector imports are read-only upstream operations and fail closed on an
  unallowlisted origin, root, repository, ref, path, alternate credentialed URL,
  unsafe redirect, oversized response, or changed content identity.
- Phase 5 adds no schema. Schema-v1-to-v4 migration fixtures and the connector
  suite run against the same current workspace initializer.
- Offline adapters use fake transports; no credential is required or exposed by
  the regression suite. Live OAuth/App provisioning remains an operator step.

## Phase 6 — Identity and Enterprise Workspace

Status: validated as an existing production boundary; final full-suite result is
recorded in the phase validation ledger below.

### Laboratory pain point

Shared kiosk identities and client-selected roles make it impossible to prove
who executed, reviewed, or approved work. The workspace boundary gives bench
users a simple local demo mode while ensuring operational deployments derive
tenant and role authority only from verified identity plus local membership.

### Validated implementation

- Central RBAC defines researcher, reviewer, lab-admin, and organization-admin
  permissions. Role-specific HTTP and browser workspaces expose only authorized
  actions.
- Generic OIDC validates RS256/ES256 signature, issuer, audience, required time
  claims, subject, and configurable tenant/role/name claims. The same standard
  boundary supports Google Workspace, Microsoft Entra ID, Auth0, and Keycloak.
- Effective roles are the intersection of signed claims and an active local
  tenant membership; disabling or narrowing a membership takes effect without
  trusting a stale role claim.
- External subjects become opaque issuer-scoped principal identifiers. Client
  payloads cannot choose tenant ownership, and cross-tenant resources remain
  non-enumerable.
- `Local Lab Admin` and other allowlisted development profiles remain available
  only outside operational scope. Operational mode fails closed unless OIDC is
  completely configured.

### Safety and migration evidence

- HTTP and WebSocket entrypoints share the identity resolver and tenant resource
  bindings. Researcher-owned experiment reads, reviewer inbox actions, connector
  administration, and aggregate admin views retain their existing permission
  gates.
- Phase 6 adds no schema. Existing organization, principal, membership, and role
  rows survive the schema-v1-to-v4 migration regression.
- Offline identity tests use generated/fake claims and never need real customer
  tokens or expose secrets.

## Phase 7 — ELN Integration

Status: implemented and tightened around the durable ExperimentSession; final
full-suite result is recorded in the phase validation ledger below.

### Laboratory pain point

Researchers often re-enter a completed bench record into an ELN, losing exact
revision identity and introducing transcription errors. A confirmed write-back
reduces that duplicate work while leaving eLabFTW as the system of record and
preserving human control over the transfer.

### Implementation

- Retained the generic `ElnConnector` boundary and production-shaped eLabFTW v2
  create-then-patch adapter with an allowlisted HTTPS origin and server-resolved
  credential.
- Bound every new write-back to one tenant-owned, completed durable
  ExperimentSession and its completed server report. Session and report protocol
  ID/runtime revision must match, and the selected lineage revision must match
  the report source/execution identity.
- Explicit confirmation is required before any claim or network call. An
  idempotency request is reserved first, finished as completed/failed, and paired
  with an append-only write-back event including session, report, lineage,
  external experiment, request hash, actor, and timestamp.
- The payload contains bounded completed steps, observations, timers,
  deviations, and source provenance. Raw audio, unrestricted transcripts,
  private model reasoning, credentials, and unpublished instructions are not
  included by default.

### Safety and migration evidence

- Schema 4 → 5 adds durable session provenance to write-back requests/events.
  Legacy rows remain with a null association; the migration does not fabricate a
  historical relationship.
- A completed legacy report without a durable session fails before the connector
  call. Session/report identity mismatches, incomplete records, unconfirmed
  requests, replayed keys, cross-tenant resources, and unsafe eLabFTW locations
  fail closed.
- Provider-free fake transports verify the exact boundary. Live eLabFTW
  credentials and tenant policy remain an operator/pilot release gate.

## Phase validation ledger

| Phase | Focused verification | Full regression | Compile | Migration | Documentation |
|---|---|---|---|---|---|
| 1. Experiment Session | 187 tests + 249 subtests passed; production WebSocket recovery passed | 741 passed + 683 subtests in 132.23s | passed | v1→v2 fixture passed | architecture map, report, migration notes, README |
| 2. Observation/Evidence | 188 tests + 250 subtests passed; voice/manual/evidence boundaries passed | 748 passed + 684 subtests in 131.44s | passed | v1→v3 and v2→v3 fixtures passed | report, migration notes, README |
| 3. Experiment Timeline | API/UI/tenant timeline tests included in Phase 2 focused gate | 748 passed + 684 subtests in 131.44s | passed | no additional schema beyond v3 | report, README, researcher timeline |
| 4. Lab Adaptation | 48 focused tests passed | 752 passed + 684 subtests in 134.89s | passed | v1→v4 and v3→v4 fixtures passed | report, migration notes, README |
| 5. Source Connectors | 39 focused tests passed | 752 passed + 684 subtests in 138.92s | passed | no schema change; v1→v4 regression passed | report, architecture/source hub, README |
| 6. Identity/Workspace | 37 focused tests passed | 752 passed + 684 subtests in 135.37s | passed | no schema change; v1→v4 regression passed | report, README, identity architecture |
| 7. ELN Integration | 25 focused tests passed | 753 passed + 684 subtests in 135.98s | passed | v4→v5 legacy-row fixture and v1→v5 regression passed | report, migration notes, README |
| 8. OCR/Document Intelligence | pending | pending | pending | pending | pending |
| 9. Computational Metadata | pending | pending | pending | pending | pending |
| 10. Product Experience | pending | pending | pending | pending | pending |
