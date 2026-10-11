# Current Architecture — VoineyLab

Date: 2026-08-24  
Scope: Controlled pilot, Cascade voice path

Revised 2026-10-08 (lane DI): one experimenter screen, no approval step.
Revised 2026-10-10 (lane CL): the approved-reference search, the safety
report tools and their hand-off worker, and the Moss connection were
deleted; the provider names below follow the settings.

## Product boundary

VoineyLab (formerly Voice Workflow Agent; the company is Voiney) is a
hands-free execution and record layer for laboratory protocols uploaded as
PDFs -- an MVP prototype, not field-validated. It is not a general chatbot, an
autonomous experiment runner, an approval authority, a full ELN/LIMS, or an
emergency/safety system.

The key architectural invariant is separation of understanding from authority:
STT and models may interpret or explain a request, while only deterministic,
server-owned code can start a protocol, change an experiment checkpoint,
confirm completion, record a controlled observation or start a timer.

```text
immutable source + evidence
        ↓
automatic OCR → automatic analysis + source-evidence check
        ↓
execution verdict: analysis passed, no execution blocker
        ↓
experimenter reads the start screen and presses "이 프로토콜로 시작"
        ↓
server starts the ExperimentSession (safety_notices_acknowledged in the ledger)
        ↓
browser PCM → Cascade STT → RequestArbitration → deterministic action gate
        ↓                                      ↓
read-only grounded guidance             confirmed server mutation
        ↓                                      ↓
canonical browser events          append-only session/report events
                                                ↓
                                     JSON / Markdown / CSV / DOCX export
```

## Authority map

| Concern | Authority | Rule |
|---|---|---|
| Request classification | `intent_arbitration.py` (`RequestArbitration`) | All learning, audit, history, uncertainty, combined, visual, current-step, and state-control requests share this boundary |
| Runtime routing | `runtime_routing.py` | Selects the curated protocol/read-only path (`route_curated_runtime_turn`); it does not mutate state. The semantic-intent fallback was removed on 2026-10-08 (lane DI); its evidence and target fences live on in `intent_fences.py` for the LLM router's validation |
| Protocol execution | `curated_protocol.py` | Owns current step, explicit start, completion confirmation, pause/resume, timers, and protocol-derived guidance |
| Durable experiment | `workspace_store.py` | Tenant-owned `ExperimentSession`, optimistic version, lifecycle, observations, evidence metadata, recovery, and append-only timeline |
| Protocol lifecycle | `protocol_catalog.py` plus `experiment_protocol*` | Immutable PDF/source identity, automatic OCR and analysis, readiness, and the execution verdict (analysis passed + no execution blocker); the experimenter's start is the one human confirmation |
| Identity and access | `identity.py` plus workspace membership | OIDC/development identity, one experimenter identity (no roles, lane DI), tenant isolation, and non-enumerable cross-tenant resources |
| Experiment report | `experiment_reports.py` | Append-only workflow/report events and JSON/Markdown/CSV/DOCX export |
| Browser | `static/index.html` and `static/app.css` | Renders canonical server state, captures voice/manual input, and stages confirmation; never derives authoritative checkpoints from prose |

## Runtime request path

1. HTTP middleware or the WebSocket handshake resolves the authenticated
   principal. Operational scope requires complete OIDC configuration; non-
   operational scope is one development identity chosen by the server (the
   page sends no profile header, lane DI).
2. The server checks the active local membership and derives the tenant.
   There are no roles. Client payloads cannot choose ownership.
3. The experimenter picks a protocol whose analysis passed and presses "이
   프로토콜로 시작". The server creates or resumes a tenant-owned experiment
   with an optimistic version fence.
4. Browser PCM is framed and admitted by WebRTC VAD. Each turn has connection,
   generation, and turn identities; barge-in and cancellation invalidate stale
   output.
5. STT output (the provider is a setting: ElevenLabs `scribe_v2` in the
   current `.env.example`) passes transcript/language admission. Empty,
   non-speech, or inconsistent input is rejected without a workflow mutation.
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
  organizations, principals, memberships (one identity, no roles)
  durable experiments + events + observations + evidence metadata
  knowledge entries + asset cards (read routes only, no screen)
  tables kept but no longer written (lane DI, 2026-10-08): protocol
    lineage + approvals, lab adaptations, source inbox, connectors +
    cursors/webhook receipts, dry-lab metadata, ELN audit, analytics,
    admin audit

experiment report database
  experiment report schema 1
  reports + append-only workflow events + finalization state
```

The legacy procedure database and its config-gated tutorial lane were deleted
on 2026-10-08 (lane DI).

The three primary SQLite stores are separate. Cross-store operations use exact
identities and fail closed, but they are not a distributed transaction. The
backup procedure therefore stops the process for a point-in-time snapshot across
databases and object bytes.

## Protocol lifecycle and the execution rule

- Source bytes and identity are immutable; a changed PDF is a new upload.
- The upload runs OCR (for pages without a text layer) and the structured
  analysis with its source-evidence check by itself.
- `available_for_execution` is "an analysis exists and carries no execution
  blocker": invalid protocol, failed source-evidence check, no executable step,
  a page still needing OCR, a safety-critical conflict. Every other readiness
  reason is a notice read on the start screen; a construct without guidance is
  announced once at its step and the source is read.
- The experimenter's press of "이 프로토콜로 시작" is the one human
  confirmation; it writes `safety_notices_acknowledged` to the ledger after
  the source's safety statements were shown beside their Korean.
- There is no approval, revocation, reviewer finding, lab adaptation or
  development activation (lane DI, 2026-10-08). Historical rows stay in the
  tables; nothing writes to them.

## Read, mutation, and failure semantics

- Read-only requests leave checkpoints unchanged.
- Completion requires the deterministic completion-intent and confirmation
  sequence; the dashboard cannot claim completion.
- Stale experiment versions and mismatched protocol revisions return conflict
  without overwriting newer state.
- Provider/model failures are visible and bounded. Tests replace providers with
  fakes; external output is validated at ingress.
- Observations are `observation_only`; evidence is `not_interpreted`. Neither
  becomes protocol knowledge; the promotion path was removed (lane DI).
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

The production adapters for the model roles (each role's provider is a
setting: openai, google, anthropic or xai), the speech providers (ElevenLabs,
Google Cloud, xAI), the OCR providers and generic OIDC are server-side and
constrained by credential/scope/origin rules. The values in use are in the
README's provider section, with the provider calls that were live-tested and
when. The protocols.io, Google Drive, GitHub and eLabFTW adapters were removed
on 2026-10-08 (lane DI); the approved-reference search, the safety report
hand-off worker and the Moss reranker on 2026-10-10 (lane CL). Contract
coverage must never be described as provider success.
