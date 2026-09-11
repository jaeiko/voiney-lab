# Productization Phase 4 — Pilot Deployment Readiness

Date: 2026-08-24  
Status: Complete  
Product disposition: Controlled Pilot Ready, Field-Unvalidated

## Outcome

The controlled-pilot deployment now has an executable state-backup and restore
procedure, broader configuration readiness checks, a tenant-scoped pilot-metrics
rollup, and authenticated evidence download with server-side integrity checks.
The product can expose completed workflows, failed commands, recovery events,
mutation failures, and user actions without collecting raw audio, transcripts,
identifiers, free text, credentials, or model reasoning in analytics.

These changes do not claim regulated validation or successful operation against
an external laboratory system. They make local deployment, recovery, evidence
handling, and pilot measurement testable and explicit.

## Implemented changes

### Reliability and recovery

- Extended `/readyz` to validate identity, workspace, protocol-catalog, and
  experiment-report configuration. Operational scope without complete OIDC now
  returns `503`; provider reachability remains outside this non-billable probe.
- Added `scripts/pilot_state_backup.py` with `create`, `verify`, and `restore`
  commands. It uses SQLite's backup API and `PRAGMA quick_check`, includes only
  SQLite/protocol-object/evidence allowlists, records SHA-256 and size in a
  versioned manifest, and refuses overwrite, relative/root sources, symlinks,
  unsafe archives, missing files, and checksum mismatches.
- Restores into a new absolute destination with component roots that can be used
  directly as protocol/workspace/report paths. The deployment runbook requires a
  stopped-process snapshot for cross-store/object consistency and a disposable
  restore drill before participant use.
- Added privacy-safe failure instrumentation for empty/non-speech commands,
  bounded turn failures, and failed report/session/observation/pause/resume/stop
  persistence. Failure metrics do not mutate workflow state and cannot make a
  failed mutation appear successful.

### Data and evidence management

- Added tenant-scoped evidence retrieval at
  `GET /api/workspace/experiments/{session_id}/evidence/{evidence_id}`.
- Retrieval validates the database association, storage path, no-link boundary,
  regular-file type, byte size, and SHA-256 before returning bytes. It uses a
  generated download name, `private, no-store`, and does not expose the original
  storage path.
- Added an evidence-download action to the experiment timeline while retaining
  the `not_interpreted` label. Evidence remains an observation artifact and
  cannot alter protocol instructions.
- Preserved existing tenant-scoped experiment timelines and JSON, Markdown, CSV,
  and DOCX report exports. The existing confirmed eLabFTW boundary remains the
  only external record write-back; no generic LIMS synchronization was invented.

### Pilot monitoring

- Added `WorkspaceStore.pilot_metrics_summary` and
  `GET /api/workspace/admin/pilot-metrics`, also embedded in the existing admin
  analytics response.
- Added admin cards for completed workflows, failed commands, recovery events,
  state-change failures, user actions, and workflow completion rate.
- Durable session/event values are explicitly lifetime counts for retained
  records. Failed-command, mutation-failure, and user-action counts explicitly
  follow the analytics-retention window.
- The response declares that raw audio, transcripts, identifiers, and free text
  are absent. Access requires the centralized analytics permission and is
  tenant-scoped.

## External integration classification

No live provider call was made during this productization phase. Historical live
evidence is retained only where the current handoff already documented it.

| Integration | Current classification | Evidence and limitation |
|---|---|---|
| xAI STT | Live-tested historically | Real 200 response and Korean round-trip evidence in `COMMERCIALIZATION_PASS4_REPORT.md`; fake-backed in this phase |
| xAI TTS | Live-tested historically | Real PCM response documented in the current handoff; fake-backed in this phase |
| Structured protocol analysis LLM | Contract-tested; live connectivity historically confirmed | Strict pipeline is fake-backed offline; historical minimal-document call returned 200 but did not complete evidence validation |
| Google Drive / Shared Drive | Contract-tested only | Fake transport and scope checks; no live OAuth/provider read |
| GitHub source connector | Contract-tested only | Fake transport/webhook tests; no live GitHub App installation |
| protocols.io | Contract-tested only | Fake transport, credential, origin, and prefix checks; no live authenticated import |
| OIDC | Contract-tested only | Signed-token behavior is tested with generated keys; no real IdP login |
| eLabFTW | Contract-tested only | Confirmed, idempotent write-back uses a fake transport; no live ELN instance |
| OCR provider | Contract-tested only | Trusted injected fake provider; no bundled/live OCR adapter |
| Snakemake / Nextflow | Metadata inspection only | Static metadata parsing; no execution path |
| Seqera / generic LIMS | Not implemented or validated | Interface/future boundary only; no claim of synchronization or execution |

The Phase 3 administrator “check configuration” action remains intentionally
classified as server credential/scope validation, not provider authentication or
reachability.

## Architecture and authority impact

- The shared `RequestArbitration` boundary is unchanged.
- Metrics observe accepted event metadata or failure outcomes; they do not grant
  authority and cannot write workflow checkpoints.
- Evidence download is a read-only `REPORT_READ` operation. Cross-tenant
  resources remain non-enumerable and the experiment timeline is unchanged.
- Backup operates outside the request path and does not rewrite live state.
- Provider failures remain visible, bounded, fake-backed in tests, and
  non-mutating.

## Verification evidence

- Focused reliability, identity, workspace, reports, connector, voice, and
  frontend suite: **246 tests + 250 subtests passed**.
- Full Python suite: **790 tests + 691 subtests passed** in 154.76 seconds.
- Playwright desktop/mobile matrix: **38 tests passed**, including researcher,
  reviewer, admin, empty/loading, responsive, and evidence-download behavior.
- `python scripts/replay_turns.py`: passed; all seven turns used the curated
  router and every replay turn remained non-mutating.
- `python -m compileall -q src tests scripts`: passed.
- `git diff --check`: passed.

## Remaining risks and deferred work

- No human wet-lab pilot, noisy-lab accessibility study, or screenshot-based
  visual/WCAG audit occurred.
- The backup tooling is validated against synthetic databases and objects, not a
  real participant dataset or organization backup platform. Encryption,
  off-host retention, restore objectives, and operator access controls remain
  deployment responsibilities.
- SQLite and process-local background jobs constrain horizontal scaling and
  multi-instance failover.
- Dedicated counters for correction/repeat rate, rejected step-omission attempts,
  and time-based abandonment are not implemented; these values must not be
  inferred from missing events.
- Evidence retrieval loads a bounded maximum of 32 MiB into memory. This is
  acceptable for the current controlled-pilot upload limit, not a large-object
  storage design.
- External connectors other than historically documented xAI STT/TTS have no
  real-service validation in this environment.

Phase 4 satisfies its exit gate for controlled-pilot deployment readiness. The
next sequential phase is commercialization readiness and documentation
reconciliation.
