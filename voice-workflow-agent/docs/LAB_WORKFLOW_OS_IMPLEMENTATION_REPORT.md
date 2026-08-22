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

## Phase validation ledger

| Phase | Focused verification | Full regression | Compile | Migration | Documentation |
|---|---|---|---|---|---|
| 1. Experiment Session | 187 tests + 249 subtests passed; production WebSocket recovery passed | 741 passed + 683 subtests in 132.23s | passed | v1→v2 fixture passed | architecture map, report, migration notes, README |
| 2. Observation/Event Timeline | pending | pending | pending | pending | pending |
| 3. Lab Adaptation | pending | pending | pending | pending | pending |
| 4. Role UX | pending | pending | pending | pending | pending |
| 5. Source Connectors | pending | pending | pending | pending | pending |
| 6. ELN Integration | pending | pending | pending | pending | pending |
| 7. OCR | pending | pending | pending | pending | pending |
| 8. Computational Metadata | pending | pending | pending | pending | pending |
| 9. Product Polish | pending | pending | pending | pending | pending |
