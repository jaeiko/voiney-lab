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

## Phase 2 — Observation and Event Timeline

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

## Phase 3 — Lab Adaptation System

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

## Phase validation ledger

| Phase | Focused verification | Full regression | Compile | Migration | Documentation |
|---|---|---|---|---|---|
| 1. Experiment Session | 187 tests + 249 subtests passed; production WebSocket recovery passed | 741 passed + 683 subtests in 132.23s | passed | v1→v2 fixture passed | architecture map, report, migration notes, README |
| 2. Observation/Event Timeline | 188 tests + 250 subtests passed; voice/manual/evidence/API/UI boundaries passed | 748 passed + 684 subtests in 131.44s | passed | v1→v3 and v2→v3 fixtures passed | report, migration notes, README, researcher timeline |
| 3. Lab Adaptation | 48 focused tests passed; typed changes, approval, immutability, IDOR and stale-parent cases passed | 752 passed + 684 subtests in 134.89s | passed | v1→v4 and v3→v4 fixtures passed | report, migration notes, README |
| 4. Role UX | pending | pending | pending | pending | pending |
| 5. Source Connectors | pending | pending | pending | pending | pending |
| 6. ELN Integration | pending | pending | pending | pending | pending |
| 7. OCR | pending | pending | pending | pending | pending |
| 8. Computational Metadata | pending | pending | pending | pending | pending |
| 9. Product Polish | pending | pending | pending | pending | pending |
