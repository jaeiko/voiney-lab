# Voice Workflow Agent Productization Phase 0 Audit

Date: 2026-08-24  
Branch: `refactor/voice-workflow-agent-stability`  
Audited HEAD: `1029bba4265255cce15cd60bef3fb50e35d46642`

## Audit status and release disposition

This audit evaluates the repository against the Productization & Commercial
Readiness goal. It is intentionally read-only except for this report. No runtime,
workflow, API, storage, or UI behavior changed during Phase 0.

The current disposition remains **Controlled Pilot Ready, Field-Unvalidated**.
The repository has unusually strong workflow-authority, provenance, safety, and
offline regression foundations for a pilot. It is not yet immediately operable
by a typical researcher, reviewer, or lab manager without product-specific
training, and it has not been validated by a real wet-lab study.

The code and automated tests support a controlled fictional or low-stakes pilot.
They do not support a claim of GLP/GMP/GxP, clinical, electronic-signature, or
regulated-production readiness.

### Evidence used

- Current source and tests at the audited HEAD, including the production
  WebSocket and HTTP boundaries.
- Current contributor contracts under `.agent/` and `AGENTS.md`.
- Current authoritative handoffs named in `AGENTS.md`, especially
  `COMMERCIALIZATION_PASS4_REPORT.md` and
  `LAB_WORKFLOW_OS_IMPLEMENTATION_REPORT.md`.
- A fresh non-browser baseline run:
  - `python -m pytest -q`: **781 passed, 691 subtests passed** in 228.04 s;
  - `python scripts/replay_turns.py`: passed with all seven replay turns
    remaining on the curated production router and only the authorized
    completion family eligible to mutate;
  - `python -m compileall -q src tests scripts`: passed;
  - `git diff --check`: passed.

The live screenshot audit is still pending. The Product Design audit contract
requires an explicitly selected browser before direct Playwright use. Static UI
inspection and existing browser-test source informed the provisional UX findings
below, but those findings are not represented as a completed visual or WCAG
audit.

## 1. Architecture assessment

### System shape

The product is a server-authoritative FastAPI application with one Cascade voice
path:

1. The browser sends 16 kHz PCM frames over `/ws`.
2. server-owned VAD and transcript-admission gates determine whether a turn is
   accepted.
3. the shared immutable `RequestArbitration` classifies the request;
4. `runtime_routing.py` sends curated protocols through the deterministic
   `CuratedProtocolSession` plan boundary;
5. explicit state, observation, timer, confirmation, identity, revision, and
   safety gates authorize any mutation;
6. canonical, generation-fenced events drive browser rendering and audio;
7. durable experiment, protocol, review, report, and integration records are
   persisted server-side.

The application currently exposes 58 HTTP routes and one WebSocket route from
`server.py`. The browser is a single server-rendered HTML/CSS/JavaScript cockpit
containing researcher, reviewer, and administrator workspaces.

### Authority and safety strengths

- The LLM has no direct state-mutation authority. Classification is evidence,
  not authorization, and `RequestArbitration.state_mutation` is always false.
- Read-only learning, audit, history, uncertainty, visual, and current-step
  requests share the production arbitration boundary.
- Combined “explain + next” requests stage explicit completion confirmation and
  do not advance on the first turn.
- The active protocol and revision are server-selected and exact-source bound.
  Stale turn/generation, revision, visual, and research results are fenced.
- Protocol ingestion preserves immutable source bytes and SHA-256 identity.
  OCR, analysis, readiness, review, approval, and operational availability are
  separate gates.
- Human approval and revocation histories are append-only. Development sources
  cannot silently become operational revisions.
- Tenant/RBAC checks are centralized, HTTP and WebSocket identity boundaries are
  aligned, and cross-tenant negative cases are tested.
- External text and images remain supplementary. Displayed external image bytes
  require rights, HTTPS/SSRF, type, size, dimension, and same-origin proxy gates.
- Metrics use privacy-safe allowlists; raw audio, transcript text, prompts,
  model reasoning, private titles, identifiers, and secrets are excluded.

### Persistence assessment

Durable state is split intentionally across several SQLite-backed domains:

- protocol catalog and immutable source objects;
- commercial workspace schema v5 for identity, lineage, approval, sessions,
  observations, evidence, adaptations, connectors, integrations, and analytics;
- append-only experiment report storage;
- the explicitly configured legacy procedure store.

The split keeps authorities bounded, but it also means cross-domain operations
cannot be one SQLite transaction. The code exposes report-persistence failure
and uses version/idempotency fences rather than pretending atomicity. A stopped
process is currently required for the documented whole-tree backup procedure.

### Runtime and operations assessment

- Health and configuration-readiness probes exist.
- The documented deployment is a directly invoked Uvicorn process under systemd.
- Invalid operational identity/configuration combinations fail closed.
- Provider tests are fake-backed offline; prior evidence live-tested xAI STT/TTS
  but not the other external integrations.
- PDF analysis jobs are process-local. Persisted lifecycle and retry make a
  restart visible, but there is no cross-process job queue.
- runtime latency/route metrics are bounded in memory; tenant analytics are
  durable but do not yet provide the complete pilot field-metrics rollup.

### Maintainability assessment

The domain boundaries are conceptually strong, but implementation concentration
is high:

- `curated_protocol.py`: 8,651 lines;
- `server.py`: 7,935 lines;
- `static/index.html`: 1,784 lines with markup and application logic combined;
- `workspace_store.py`: 3,766 lines.

This is not a reason for a rewrite. It is a reason to extract narrow, tested
presentation and service boundaries as each productization phase touches them.
The older procedure authority is documented and regression-tested as isolated,
but its long-term keep/retire decision remains open.

## 2. Product gap analysis

### Researcher experience

| Requirement | Current evidence | Gap |
|---|---|---|
| Know the experiment | Protocol selector and current-step card exist | The start area calls protocols “experiment PDFs,” and the protocol title is not presented as a concise experiment identity summary |
| Know the exact version | Revision appears in collapsed developer details and some timelines | Raw revision identity is not paired with a human-readable version explanation at the start decision |
| Know who approved it | Workspace protocol data can expose a reviewer principal, and catalog approval events retain actor evidence | The bench start flow does not show final approval actor/time; development-only fixtures must be clearly identified as not finally approved |
| Know the current step | Strong persistent current-step card and canonical state rendering | Session selection uses raw `Step` and status values in several places and does not explain restored state |
| Know available actions | Start/stop/pause and voice examples exist | Available actions are distributed across the sticky rail, setup panel, step card, ledger, and examples instead of summarized in context |
| Understand voice state | Friendly detail text exists | Visible primary labels still expose `IDLE`, `LISTENING`, `THINKING`, and `SPEAKING` |
| Recover from errors | Network and identity errors are generally actionable | The UI does not consistently state that workflow state was not changed after every recoverable voice/provider failure |
| Resume after refresh | Durable exact-revision recovery, optimistic versioning, and manual experiment selection exist | Refresh does not automatically explain what was restored, what was deliberately not restored (pending confirmations, conversation, active timers), and what to do next |

The completion workflow itself is a product strength and must not change:
voice command → shared intent evidence → deterministic validation → explicit
confirmation when required → server mutation → canonical event rendering.

### Reviewer experience

| Requirement | Current evidence | Gap |
|---|---|---|
| Review inbox | New/changed source rows exist | The UI shows connector/version/time but omits protocol title, requester, change reason, and risk/readiness state even where source/store data exists |
| Change summary | A `change_summary` is stored and a raw unified JSON diff is available | The UI ignores the summary and lets the technical diff dominate; experimental impact and unassessed risk are not called out |
| Clear responsibility | Comments are mandatory and approval is deterministic | The interface does not explain the reviewer’s responsibility or the consequence of each action |
| Decision actions | Approve, reject, and revoke are supported | Product terms are “reject” and “revoke,” not “Request Revision” and “Disable Future Use”; there is no clear confirmation/impact copy |
| Audit history | Append-only approval history includes actor, role, comment, affected revision, and timestamp | The reviewer workspace does not render it |

Risk must never be inferred from prose or a diff. When no approved risk
assessment exists, the product should display **Not assessed** and prevent the
UI from implying otherwise.

### Lab administrator experience

| Requirement | Current evidence | Gap |
|---|---|---|
| User management | Central roles and membership APIs exist | The UI exposes `Principal ID`, `OIDC subject`, and raw role enums |
| Connector setup | Allowlisted connector kinds, server-side secret references, and scoped roots exist | The UI asks a non-developer to type `secret://` references and saves connectors enabled by default; there is no authenticate → scope → test → enable progression |
| Connection confidence | Connector lists truthfully say unvalidated/contract-tested | There is no durable last test, failure reason, or verified/not-verified status |
| Security visibility | Retention and privacy-safe metrics exist | Access history, connector failures, backup status, and security posture are not presented as operator tasks |
| Permission clarity | RBAC is strong in code | Product-facing permission names and consequences are missing |

The connector flow is the most important admin risk. “Configured” must not be
presented as “working,” and a connector must not become usable merely because a
secret reference and root were saved.

### Pilot operations and commercialization

- A pilot package, deployment runbook, voice field-evaluation plan, CI, health
  probes, exports, and backup instructions exist.
- No human wet-lab session, noisy-lab/accent study, real OIDC deployment, real
  eLabFTW write-back, real OCR provider, or real Drive/GitHub/protocols.io
  integration has been validated in this environment.
- Backup/restore is documented but has not been rehearsed on a pilot dataset.
- Runtime metrics are process-local, and requested workflow/failure/recovery
  KPIs are not yet available in one durable operator view.
- There is no container artifact; the tested deployment path is systemd.
- `ARCHITECTURE_MAP.md` explicitly describes a pre-extension snapshot. The
  current architecture is split between README, `.agent/architecture.md`, and
  handoff reports rather than one current product architecture document.
- There is no dedicated capability matrix, troubleshooting guide, or user guide.
- The current handoff-of-record is Pass 4, while HEAD contains later commits and
  a Pass 5 tag without a corresponding current handoff report. Documentation
  authority therefore lags the audited code.

## 3. Prioritized technical debt list

### P0 — required before a real pilot

1. Productize experiment identity, approval context, voice-state wording, and
   resume explanation without changing workflow authority.
2. Productize the reviewer projection around change reason, impact, explicit
   risk state, decision consequences, and approval history.
3. Replace raw admin identity/role/secret language and introduce a fail-closed
   configured → tested → enabled connector lifecycle.
4. Add production-boundary browser tests for the actual start, resume, reviewer
   decision, admin membership, connector failure, and mobile flows. Current E2E
   coverage is primarily presence/layout coverage (16 declared test cases across
   four specs), not end-to-end task completion.
5. Capture current-run desktop and narrow-screen screenshots and perform a
   combined UX/accessibility audit. Static inspection cannot verify focus order,
   contrast, reflow, target size, live-region behavior, or modal focus trapping.
6. Implement the durable, privacy-safe pilot KPI rollup for workflow completion,
   failed commands, recoveries, mutation failures, and user actions.
7. Rehearse backup/restore and incident/abort procedures before pilot use.

### P1 — required for repeatable commercial pilots

1. Reduce change risk in the large `server.py`, `curated_protocol.py`,
   `workspace_store.py`, and inline browser application by extracting only the
   bounded services/presenters touched by each phase.
2. Move long-running analysis from process-local background tasks to a durable
   worker/job boundary with restart and duplicate-execution tests.
3. Decide whether the isolated legacy procedure lane remains a supported
   tutorial mode or is retired with an explicit migration path.
4. Persist operational latency/failure summaries across process restarts while
   retaining the current privacy allowlists.
5. Live-validate OIDC and one real downstream/customer integration under an
   approved data-handling policy.

### P2 — deliberate later work

- Multi-process/region deployment, SCIM, connector SDK, and richer support tools.
- Reviewed conditionals, repeats, parallel steps, multi-day handoffs, and
  multilingual facility packs.
- Any regulated-compliance claim, autonomous approval, safety decision,
  protocol modification, equipment control, or computational execution.

## 4. Sequential implementation roadmap

### Phase 1 — Researcher experience productization

1. Add a server-backed experiment identity projection: protocol title, exact
   revision/version, approval state and actor/time when available, current step,
   and available actions.
2. Map internal voice states to product labels (`Ready`, `Listening`,
   `Understanding request`, `Providing guidance`) while preserving canonical
   event values internally.
3. Add explicit resume disclosure: restored exact revision/current/completed
   steps; not restored pending confirmations, conversation, or active timers;
   next available action.
4. Improve recoverable error copy to state that no workflow change was assumed.
5. Add API/frontend/Playwright tests for fresh start, exact-revision selection,
   ambiguous/multiple resumable sessions, refresh recovery, stale versions,
   completion confirmation, desktop, and narrow layouts.

Exit gate: a first-time researcher can answer the five start-context questions
from the primary bench view, and no read-only/error/resume rendering changes a
checkpoint.

### Phase 2 — Reviewer experience productization

1. Enrich the existing reviewer projection with protocol title, requester,
   source/change reason, explicit readiness/risk status, and append-only history.
2. Render a deterministic impact-first summary before the raw technical diff.
3. Present actions as Approve, Request Revision, and Disable Future Use, with
   clear consequences and explicit confirmation; map them to the existing
   approved/rejected/revoked authority rather than creating a new authority.
4. Add production-boundary tests for roles, stale/replayed decisions, development
   sources, adaptations, history, and responsive decision flow.

Exit gate: a reviewer can state what changed, why, known impact/risk, and what
each decision will do before submitting it.

### Phase 3 — Lab administrator productization

1. Replace developer identity and raw role labels with product terminology while
   keeping stable IDs available in an advanced-details disclosure.
2. Add a connector lifecycle with separate configuration, credential resolution,
   scoped-access validation, fake-backed connection testing, explicit enable,
   and append-only status/failure evidence. Never claim a live test from local
   validation alone.
3. Add permission explanations, connection state, retention, and privacy-safe
   access/failure visibility.
4. Add migration, API, IDOR/RBAC, failure, secret-leakage, and browser tests.

Exit gate: a non-developer admin can add a user and configure a connector without
seeing secret values or accidentally enabling an untested connection.

### Phase 4 — Pilot deployment readiness

1. Implement the durable pilot KPI rollup and recovery/mutation-failure metrics.
2. Add tested backup/restore verification and an operator-visible last-success
   result without storing sensitive content.
3. Harden restart behavior for long-running jobs and document single-process
   limits.
4. Verify experiment exports, evidence metadata, audit history, CSV/report output,
   and one approved integration boundary with offline fakes; live validation
   remains opt-in and credential-gated.
5. Run the researcher/reviewer/admin desktop/mobile acceptance suite and prepare
   the owner’s manual pilot checklist.

Exit gate: operators can detect failure, recover the service and state, measure
pilot outcomes, and distinguish configured, tested, and live-validated systems.

### Phase 5 — Commercialization readiness

1. Publish a current architecture document, capability matrix, pilot deployment
   guide, troubleshooting guide, and role-based user guide.
2. Mark pre-extension and phase-numbered material historical where appropriate;
   update the handoff-of-record to the actual audited/implemented HEAD.
3. Complete only evidence-driven maintainability extractions and deployment
   improvements that can be verified in the available environment.
4. Run all required regression, replay, compile, diff, and browser gates and
   deliver the final productization report and manual verification checklist.

Exit gate: current capabilities, limitations, deployment, support, and pilot
verification are understandable without reconstructing history from multiple
phase reports.

## Phase 0 conclusion

The architecture should be preserved and incrementally productized. The largest
commercial risk is not missing AI capability; it is that strong internal safety
and provenance controls are still presented through developer-oriented and
fragmented workflows. Phases 1–3 should therefore focus on trustworthy
projections and explicit operator consequences, while Phases 4–5 convert the
existing engineering evidence into measurable and supportable pilot operations.
