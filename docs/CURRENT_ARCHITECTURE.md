# Current Architecture — Voice Workflow Agent

Date: 2026-08-24  
Scope: Controlled pilot, Cascade voice path

## Product boundary

Voice Workflow Agent is a hands-free execution and record layer for approved
laboratory protocols. It is not a general chatbot, an autonomous experiment
runner, an approval authority, a full ELN/LIMS, or an emergency/safety system.

The key architectural invariant is separation of understanding from authority:
STT and models may interpret or explain a request, while only deterministic,
server-owned code can approve a revision, change an experiment checkpoint,
confirm completion, record a controlled observation, start a timer, or write to
an ELN.

```text
immutable source + evidence
        ↓
analysis / OCR review / typed adaptation
        ↓
human review → exact approved revision
        ↓
researcher selects revision and starts ExperimentSession
        ↓
browser PCM → Cascade STT → RequestArbitration → deterministic action gate
        ↓                                      ↓
read-only grounded guidance             confirmed server mutation
        ↓                                      ↓
canonical browser events          append-only session/report events
                                                ↓
                              export / confirmed ELN write-back
```

## Authority map

| Concern | Authority | Rule |
|---|---|---|
| Request classification | `intent_arbitration.py` (`RequestArbitration`) | All learning, audit, history, uncertainty, combined, visual, current-step, and state-control requests share this boundary |
| Runtime routing | `runtime_routing.py` | Selects the curated protocol/read-only path; it does not mutate state |
| Protocol execution | `curated_protocol.py` | Owns current step, explicit start, completion confirmation, pause/resume, timers, and protocol-derived guidance |
| Durable experiment | `workspace_store.py` | Tenant-owned `ExperimentSession`, optimistic version, lifecycle, observations, evidence metadata, recovery, and append-only timeline |
| Protocol lifecycle | `protocol_catalog.py` plus `experiment_protocol*` | Immutable PDF/source identity, extraction/analysis/OCR lifecycle, readiness, approval, and revocation |
| Identity and access | `identity.py` plus workspace membership | OIDC/development identity, centralized role permissions, tenant isolation, and non-enumerable cross-tenant resources |
| Experiment report | `experiment_reports.py` | Append-only workflow/report events and JSON/Markdown/CSV/DOCX export |
| External source reads | `protocol_sources.py` | Bounded protocols.io, Drive, and GitHub contracts; imported content is untrusted until reviewed |
| ELN write-back | `eln_connectors.py` plus server/workspace gates | Explicitly confirmed, idempotent eLabFTW projection from a completed matching session/report |
| Browser | `static/index.html` and `static/app.css` | Renders canonical server state, captures voice/manual input, and stages confirmation; never derives authoritative checkpoints from prose |

## Runtime request path

1. HTTP middleware or the WebSocket handshake resolves the authenticated
   principal. Operational scope requires complete OIDC configuration; non-
   operational scope may use only server-allowlisted development profiles.
2. The server intersects verified identity roles with the active local
   membership and derives the tenant. Client payloads cannot choose ownership.
3. The researcher selects an executable exact revision. The server creates or
   resumes a tenant-owned experiment with an optimistic version fence.
4. Browser PCM is framed and admitted by WebRTC VAD. Each turn has connection,
   generation, and turn identities; barge-in and cancellation invalidate stale
   output.
5. xAI STT output passes transcript/language admission. Empty, non-speech, or
   inconsistent input is rejected without a workflow mutation.
6. Shared arbitration classifies the request. Read-only questions remain read-
   only. A combined explanation/next request stages a completion confirmation.
7. Deterministic protocol logic validates identity, revision, current step,
   expected version, observation/timer requirements, confirmation, and safety.
8. Only an accepted server mutation is persisted. Failed persistence restores or
   retains the last authoritative checkpoint and returns bounded recovery text.
9. Canonical events update the browser; model prose is never parsed back into
   state.

## Persistent topology

```text
protocol data directory
  protocol_workspace.sqlite (schema 1)
  objects/sha256/... immutable protocol PDFs/assets

workspace data directory
  commercial_workspace.sqlite (schema 6)
  evidence/<tenant>/<session>/... opaque attached bytes
  organizations, principals, memberships
  protocol lineage + append-only approvals
  durable experiments + events + observations + evidence metadata
  connectors + validation lifecycle + cursors/webhook receipts
  knowledge/assets/dry-lab metadata/ELN audit
  privacy-safe analytics + append-only admin audit

experiment report database
  experiment report schema 1
  reports + append-only workflow events + finalization state

optional legacy procedure database
  explicitly configured tutorial lane only
```

The three primary SQLite stores are separate. Cross-store operations use exact
identities and fail closed, but they are not a distributed transaction. The
backup procedure therefore stops the process for a point-in-time snapshot across
databases and object bytes.

## Protocol and approval lifecycle

- Source bytes and identity are immutable; a change produces a new revision.
- Parsing, structural readiness, OCR acceptance, hazard review, human approval,
  and operational authorization are distinct gates.
- OCR is a trusted injected provider boundary. Accepted OCR text is eligible for
  later analysis, not automatically approved or executable.
- Lab adaptations are typed child revisions and use the same reviewer approval
  path. A competing approval classifier or shortcut does not exist.
- Revocation blocks new operational sessions and preserves historical lineage.

## Read, mutation, and failure semantics

- Read-only requests leave checkpoints unchanged.
- Completion requires the deterministic completion-intent and confirmation
  sequence; the dashboard cannot claim completion.
- Stale experiment versions and mismatched protocol revisions return conflict
  without overwriting newer state.
- Provider/model failures are visible and bounded. Tests replace providers with
  fakes; external output is validated at ingress.
- Observations are `observation_only`; evidence is `not_interpreted`. Neither
  becomes protocol knowledge without a separate reviewed promotion path.
- Evidence downloads recheck tenant/session association, path containment,
  absence of links, file type, byte size, and SHA-256.
- Metrics and logs use privacy-safe allowlists. Raw audio, transcripts,
  identities, free text, secrets, and model reasoning are excluded from pilot
  analytics.

## Process and scaling model

The tested deployment is one FastAPI/Uvicorn process managed by systemd. Health
and readiness probes, an executable backup/restore tool, and local/CI regression
suites support a controlled pilot. SQLite, process-local protocol analysis jobs,
and the absence of an external job queue or object store make horizontal scaling,
automatic failover, and regulated availability out of scope.

## External integration boundary

The production adapters for xAI, protocols.io, Google Drive, GitHub, generic
OIDC, and eLabFTW are server-side and constrained by credential/scope/origin
rules. The administrator configuration check proves only local credential
availability and scope syntax. See `CAPABILITY_MATRIX.md` for current live-test
classification; contract coverage must never be described as provider success.
