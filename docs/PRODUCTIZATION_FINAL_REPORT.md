# Voice Workflow Agent — Productization Final Report

Date: 2026-08-24  
Roadmap: Phases 0–5 complete  
Disposition: **Controlled Pilot Ready, Field-Unvalidated**

## 1. Executive summary

Voice Workflow Agent has been advanced from a technically capable prototype to
a coherent controlled-pilot product foundation. The work preserved the defining
architecture—server-owned state, shared deterministic request arbitration,
human approval, exact protocol provenance, fail-closed mutation, and Cascade-
only voice—while improving the three role experiences and adding operational
recovery, evidence retrieval, pilot metrics, backup/restore, and current product
documentation.

A researcher can now see what experiment/version/approval/current step is active,
understand voice and recovery state, resume only an explicitly selected open
experiment, and retrieve integrity-checked evidence. A reviewer receives an
impact-first decision packet with explicit consequences and append-only history.
A laboratory administrator can understand users and permissions, follow a
disabled → configuration check → enable connection lifecycle, inspect security
activity, and measure a controlled pilot without exposing sensitive content.

This is not a regulated release, autonomous science system, full ELN/LIMS, or
high-availability platform. No human wet-lab study occurred during this work.

## 2. Product maturity assessment

| Dimension | Assessment | Evidence / gate |
|---|---|---|
| Workflow correctness | Pilot-ready | Deterministic confirmation and stale-version gates, non-mutation replay, durable experiment recovery |
| Researcher usability | Pilot-ready, field-unvalidated | Clear context/action/recovery UI and desktop/mobile acceptance; no noisy-lab study |
| Reviewer governance | Pilot-ready | Impact-first packet, allowed-action enforcement, confirmation, immutable decision history |
| Administrator operations | Pilot-ready | Effective permissions, connection lifecycle, audit/security posture, retention and metrics |
| Data traceability | Pilot-ready | Exact source/revision binding, append-only events/reports, evidence hash verification, exports |
| Deployment/recovery | Controlled-pilot ready | Probes, systemd path, executable verified backup/restore; no HA or live-dataset restore evidence |
| External integrations | Mixed | xAI STT/TTS historically live-tested; other provider boundaries contract-tested or unavailable as classified |
| Security/privacy | Strong pilot boundary | OIDC fail-closed operational mode, tenant RBAC/IDOR tests, opaque credentials, allowlisted analytics |
| Accessibility/field fit | Not validated | Responsive automated tests passed; screenshot/WCAG and real bench/noise studies remain open |
| Regulatory readiness | Not ready/claimed | No electronic signature, GLP/GMP/clinical validation, formal QMS evidence, or regulated controls |

The correct maturity statement remains **Controlled Pilot Ready,
Field-Unvalidated**.

## 3. Implemented improvements

### Phase 0 — Audit

- Assessed backend, workflow authority, persistence, voice/WebSocket pipeline,
  role interfaces, tests, commercial gaps, and technical debt.
- Established a prioritized sequential roadmap without changing product behavior.

### Phase 1 — Researcher experience

- Added approval context and deterministic recovery projections.
- Reworked protocol/experiment context so name, exact version, approval, current
  step, and valid next actions are immediately visible.
- Replaced raw voice-state terminology with researcher-facing language and
  actionable, non-leaking failure recovery.
- Distinguished new start from explicit resume and disclosed restored versus
  intentionally non-restored state.

### Phase 2 — Reviewer experience

- Added requester, reason, protocol/version, change summary, experimental impact,
  risk/unknown-risk treatment, allowed decisions, and audit history.
- Made approve, request revision, and disable future use consequences explicit.
- Added strict transition/stale fences and a separate confirmation step; OCR
  acceptance remains visibly distinct from protocol approval.

### Phase 3 — Administrator experience

- Added friendly role/permission projections and product terminology.
- Added a five-stage connection flow with tenant-scoped opaque credential
  handles, disabled/untested defaults, configuration validation, stale-safe
  enablement, and connector scope enforcement.
- Migrated workspace storage to schema 6 and added append-only administrative
  audit plus security/retention/connection posture.

### Phase 4 — Pilot deployment readiness

- Added tenant pilot metrics for completed workflows, failed commands, recovery
  events, mutation failures, user actions, and completion rate.
- Instrumented bounded voice and persistence failures without granting authority
  or recording sensitive content.
- Added authenticated evidence download with tenant, path/no-link, regular-file,
  size, and SHA-256 validation.
- Added executable privacy-allowlisted SQLite/object/evidence backup, archive
  verification, and fresh-directory restore tooling.
- Extended readiness to validate identity, workspace, protocol, and report
  configuration.

### Phase 5 — Commercialization foundation

- Added current architecture, capability matrix, user guide, pilot deployment
  guide, troubleshooting guide, and documentation index.
- Reconciled README/runbook/readiness/implementation claims with current code and
  separated current authority from historical phase evidence.
- Documented maintainability hotspots and an incremental, evidence-led
  engineering sequence.

## 4. Files changed

### Product code

- `src/voice_workflow_agent/identity.py` — canonical effective-permission
  projection.
- `src/voice_workflow_agent/protocol_catalog.py` — approval context and reviewer
  product projections.
- `src/voice_workflow_agent/workspace_store.py` — schema-6 connector lifecycle,
  audit, role/review/recovery projections, evidence read boundary, and pilot
  metrics.
- `src/voice_workflow_agent/server.py` — APIs, readiness, failure metrics,
  evidence retrieval, reviewer/admin/researcher production boundaries.
- `src/voice_workflow_agent/static/index.html` and `static/app.css` — role UX,
  state/recovery language, confirmation, metrics, and evidence action.
- `scripts/pilot_state_backup.py` — create/verify/restore utility.

### Tests

- Updated researcher/reviewer/admin/empty-state Playwright specifications.
- Updated protocol catalog, experiment session, workspace, ELN, adaptation,
  frontend, server helper, and curated voice-turn tests.
- Added `tests/test_pilot_backup.py`.

### Documentation

- Added `PRODUCTIZATION_PHASE0_AUDIT.md`, phase 1–5 reports, this final report,
  `CURRENT_ARCHITECTURE.md`, `CAPABILITY_MATRIX.md`, `PILOT_DEPLOYMENT_GUIDE.md`,
  `TROUBLESHOOTING_GUIDE.md`, `USER_GUIDE.md`, and `DOCUMENTATION_INDEX.md`.
- Updated `README.md`, `DEPLOYMENT_RUNBOOK.md`,
  `PILOT_READINESS_PACKAGE.md`, and
  `LAB_WORKFLOW_OS_IMPLEMENTATION_REPORT.md`.

The pre-existing untracked `docs/demo_script.md` and externally created
`.playwright-mcp/` directory were preserved and are not part of this work.

## 5. Architecture impact

- Server authority and the shared `RequestArbitration` boundary remain intact;
  no client or model output can directly mutate state.
- The durable workspace schema advances to version 6 through forward migrations.
  Existing records are preserved; migrated enabled connectors become untested
  until explicitly checked.
- All new read/mutation operations use centralized RBAC and tenant ownership.
  Evidence download is read-only; metrics are aggregate/read-only; connection
  and decision changes are deterministic and audited.
- Browser rendering continues to use safe text insertion and canonical server
  events. New UI confirmation stages do not replace server validation.
- Backup/recovery tooling is outside the request path and copies only explicit
  durable-state allowlists.
- No Native/Realtime voice path, competing classifier, autonomous approval,
  generic LIMS sync, or dry-lab execution plane was added.

## 6. Tests executed

- Phase 0 baseline: 781 tests + 691 subtests.
- Phase 1 final: 782 tests + 691 subtests; 32 browser tests.
- Phase 2 final: 783 tests + 691 subtests; 34 browser tests.
- Phase 3 final: 786 tests + 691 subtests; 36 browser tests.
- Phase 4/5 final Python regression: **790 tests + 691 subtests passed**.
- Focused Phase 4 reliability/integration suite: **246 tests + 250 subtests
  passed**.
- Final browser matrix after all frontend changes: **38/38 passed** across
  Desktop Chrome and Mobile Chrome.
- `python scripts/replay_turns.py`: seven curated turns passed with zero state
  mutation.
- `python -m compileall -q src tests scripts`: passed.
- `git diff --check`: passed.
- Local Markdown link validation across README and top-level docs: passed.

All external-provider behavior in automated tests was fake-backed. No new live
external request was made in this productization run.

## 7. Remaining risks

- No human wet-lab, noisy-room voice, accessibility, focus-order, or screenshot-
  based WCAG validation has occurred.
- External OIDC, Drive, GitHub, protocols.io, eLabFTW, and OCR integrations lack
  real tenant/account validation. Administrator configuration check is not a
  provider call.
- Backup/restore is tested with synthetic state, not a real pilot dataset or the
  organization's encrypted off-host platform.
- SQLite, local object storage, three-store consistency, and process-local jobs
  limit multi-instance scaling, automatic failover, and availability targets.
- `server.py`, `workspace_store.py`, and the single-file frontend are maintainable
  under the current regression suite but are large coupling hotspots.
- Dedicated correction/repeat, rejected-step-attempt, and time-based abandonment
  metrics are not present.
- Fixed roles do not support custom/delegated laboratory authorization models.
- Evidence retrieval is bounded to 32 MiB and memory-buffered; it is not a large-
  object serving design.
- Regulatory/electronic-signature, security assessment, privacy/legal review,
  disaster-recovery objectives, and facility validation remain external gates.

## 8. Manual verification checklist

### Researcher

- [ ] Select an approved protocol and verify experiment, version, approver,
  current step, and available actions.
- [ ] Start a new experiment and verify Ready → Listening → Understanding request
  → Providing guidance states.
- [ ] Ask a read-only current-step/why/warning question and confirm no mutation.
- [ ] Request the next step, complete the confirmation, and verify exactly one
  durable transition.
- [ ] Record a manual/voice observation and attach/download evidence; verify
  observation-only/not-interpreted labels.
- [ ] Pause, refresh/reconnect, explicitly select the session, and verify the
  restored/not-restored disclosure before resuming.

### Reviewer

- [ ] Open a pending request and verify requester, reason, protocol, version,
  change, impact, risk, source, and technical diff.
- [ ] Request a revision and verify the consequence/history.
- [ ] Approve a fresh eligible revision through explicit confirmation.
- [ ] Attempt a stale competing decision and verify it is rejected.
- [ ] Disable future use and verify new sessions are blocked while history is
  preserved.
- [ ] Verify OCR acceptance cannot be confused with protocol approval.

### Administrator

- [ ] Add/update a test user and verify friendly permission level plus effective
  allowed actions.
- [ ] Create a connector and verify it begins disabled/untested.
- [ ] Select a secure credential and narrow scope, run configuration check, and
  verify enable appears only after success.
- [ ] Confirm no credential value/reference is visible and that the check does
  not claim live provider success.
- [ ] Review activity, connection posture, retention, and pilot-metric privacy.
- [ ] Create, verify, restore, and smoke-test a pre-pilot backup in a fresh path.

### Cross-role and operations

- [ ] Confirm `/healthz` and `/readyz` on the exact pilot configuration.
- [ ] Exercise empty speech, stale version, provider failure, and service restart;
  verify no unconfirmed mutation.
- [ ] Export JSON, Markdown, CSV, and DOCX from a completed test record.
- [ ] If explicitly in scope, run one bounded authorized external integration
  validation and record whether it is configuration, connectivity, or full
  end-to-end evidence.
- [ ] Review abort criteria and incident handling with every participant.

## 9. Recommended next steps

1. **Run one supervised, non-hazardous wet-lab pilot** with a real researcher,
   reviewer, and administrator; collect the defined metrics, incidents, and
   interviews.
2. **Complete accessibility and noisy-lab validation**, including microphone
   recovery, keyboard/focus behavior, screen reader labels, contrast, gloves/
   distance usability, accents, and background noise.
3. **Perform deployment-specific restore evidence** using encrypted off-host
   storage and agreed RPO/RTO; rehearse operator ownership and incident response.
4. **Live-validate only the integrations required by the pilot**—real OIDC first,
   then the chosen source connector and eLabFTW/OCR if in scope—with bounded,
   privacy-safe acceptance records.
5. **Resolve the scaling/product boundary** before broader rollout: remain a
   single-instance SQLite pilot product or design a deliberate database, object
   store, job queue, and migration architecture.
6. **Refactor hotspots incrementally after field evidence**, beginning with
   cohesive route/service extraction from `server.py`; retain every authority
   and production-boundary regression test.
7. **Decide the legacy procedure lane disposition** and formalize security,
   privacy, compliance, support, and release ownership before any regulated or
   unsupervised use.
