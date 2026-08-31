# Voice Workflow Agent

[![CI](https://github.com/jaeiko/voice-workflow-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/jaeiko/voice-workflow-agent/actions/workflows/ci.yml)

Canonical repository: [`jaeiko/voice-workflow-agent`](https://github.com/jaeiko/voice-workflow-agent).
This project originated inside the course repository
[`jaeiko/voice-ai-course`](https://github.com/jaeiko/voice-ai-course) (a fork
of `civiliangame/voice-ai-course`); that repository is preserved as historical
record but is no longer where active development happens.

Voice Workflow Agent is a voice-first laboratory protocol knowledge and
execution layer. Its product wedge is:

> reviewed protocol source → hands-free bench execution → auditable experiment
> record → integration

It combines immutable source ingestion, human-controlled protocol lifecycle,
deterministic workflow mutation, source-grounded voice guidance, and controlled
downstream write-back. It is a controlled-pilot system—not a validated
GLP/GMP/clinical system, a full ELN/LIMS, an autonomous scientist, or a safety
authority.

## Product contract

- The active voice path is Cascade: browser PCM → WebRTC VAD → xAI STT → shared
  intent arbitration → deterministic workflow/tool boundary → xAI TTS.
- Protocol learning, audit, history, combined “why + next,” visual requests,
  completion, pause/resume, and interruption stay on the production WebSocket
  path covered by integration tests.
- Model prose cannot advance a step, record an observation, start a timer,
  approve a protocol, resume blocked work, or write to an ELN.
- Read-only questions do not mutate workflow state. Combined explanation/next
  requests explain and preview, then wait for explicit completion.
- Every executable session is pinned to an exact protocol revision and source
  identity. Source changes create drafts; they never overwrite an approved
  revision or rebind a running experiment.
- Parsing, structural readiness, hazard review, human approval, and operational
  authorization are separate gates.
- Raw audio, unrestricted transcripts, prompts, model reasoning, and connector
  secrets are excluded from persistent pilot analytics.

## Architecture

```text
Local PDF / protocols.io / Drive / GitHub
  → source connector boundary
  → immutable source identity + tenant-scoped lineage revision
  → inbox + source/evidence review + diff
  → reviewer decision / explicit non-operational development activation
  → exact executable protocol revision

Browser AudioWorklet (16 kHz PCM)
  → FastAPI WebSocket + FrameBuffer + WebRTC VAD
  → xAI POST /v1/stt
  → language-consistency and transcript-admission gates
  → shared RequestArbitration
      ├─ emergency and deterministic state gates
      ├─ curated/executable protocol router
      ├─ approved reference retrieval
      └─ bounded general agent tool loop
  → sentence-segmented xAI TTS
  → canonical events and browser playback

Canonical workflow events
  → tenant-owned persistent ExperimentSession
  → append-only experiment timeline and report
  → JSON / Markdown / CSV / DOCX export
  → explicit confirmed eLabFTW write-back
  → tenant-scoped privacy-safe aggregates
```

The main runtime routing boundary is
`src/voice_workflow_agent/runtime_routing.py`. An optional, disabled-by-default
semantic intent fallback (`semantic_intent.py`) sits behind it: when
deterministic routing returns a catch-all, it may *propose* one of the existing
bounded workflow actions, and server-owned policy in the same boundary decides
whether that proposal is used. It never mutates workflow state - see
[Semantic intent fallback](#semantic-intent-fallback). Tenant/RBAC logic is in
`identity.py` and `workspace_store.py`. Protocol source adapters are in
`protocol_sources.py`; computational metadata is in `drylab_workflows.py`; the
ELN boundary is in `eln_connectors.py`.

The current component, authority, and persistence design is documented in
[`docs/CURRENT_ARCHITECTURE.md`](docs/CURRENT_ARCHITECTURE.md). The older
[`docs/ARCHITECTURE_MAP.md`](docs/ARCHITECTURE_MAP.md) is a labeled
pre-extension snapshot. Current capabilities and documentation authority are
indexed in [`docs/DOCUMENTATION_INDEX.md`](docs/DOCUMENTATION_INDEX.md).
Implementation and phase evidence are tracked in
[`docs/LAB_WORKFLOW_OS_IMPLEMENTATION_REPORT.md`](docs/LAB_WORKFLOW_OS_IMPLEMENTATION_REPORT.md),
with forward-only storage details in
[`docs/MIGRATION_NOTES.md`](docs/MIGRATION_NOTES.md).

For operators and pilot participants, start with the
[`Pilot Deployment Guide`](docs/PILOT_DEPLOYMENT_GUIDE.md),
[`User Guide`](docs/USER_GUIDE.md), and
[`Troubleshooting Guide`](docs/TROUBLESHOOTING_GUIDE.md).

## Persistent experiment sessions

When workspace mode is enabled, every accepted voice configuration is bound to a
tenant-owned `ExperimentSession` pinned to the exact protocol revision. The
session begins in `ready`; the existing deterministic START intent moves it to
`in_progress`. Step completions update an append-only completed-step set and the
current-step recovery projection. Pause, resume, stop, block, and completion are
explicit lifecycle events.

A WebSocket reconnect may supply the server-issued `experiment_session_id` and
`experiment_session_version`. Recovery succeeds only for the original protocol
and revision and a fresh optimistic version. The server restores only contiguous
completed steps and the authoritative current step. It does not restore pending
confirmations, model output, conversation history, or active timers.

`GET /api/workspace/experiments` lists sessions visible to the current role;
`GET /api/workspace/experiments/{session_id}` returns the durable event history.
The dashboard transition endpoint permits explicit pause/resume/stop/block but
cannot claim completion—completion remains a protocol-authority action.

Each started session also has an append-only observation timeline. Researchers
can say “메모 추가해: …”, say “record observation” and answer the bounded
follow-up, or add a manual note from the bench workspace. Observation wording is
stored with its exact protocol step, author, category, capture source, and
timestamp as `observation_only`; it never becomes an instruction or approved
protocol fact. Source-defined positive/negative observations continue through
the existing completion gates.

Images and documents can be attached as opaque evidence. Uploads are streamed,
capped at 32 MiB, hashed, and stored by content identity. The system records
metadata with `not_interpreted` status and does not run OCR or image/document
interpretation. Internal storage references are not returned by JSON APIs or the
timeline UI. An authorized tenant member can download the original bytes through
a generated same-origin link; the server rechecks storage containment, regular-
file type, recorded size, and SHA-256 before returning it with `no-store`.

Timeline endpoints are:

- `GET /api/workspace/experiments/{session_id}/timeline`;
- `POST /api/workspace/experiments/{session_id}/observations`;
- `POST /api/workspace/experiments/{session_id}/evidence`;
- `GET /api/workspace/experiments/{session_id}/evidence/{evidence_id}`; and
- `POST /api/workspace/reviewer/experiments/{session_id}/actions`.

## Protocol onboarding and lifecycle

The browser implements the explicit lifecycle:

```text
uploaded
  → analysis_pending
  → analyzing
  → analysis_ready
  → review_required
  → executable_draft OR blocked
  → approved
  → revoked
```

1. `POST /api/protocols?filename=...` streams a PDF to a bounded temporary file,
   validates its type/size/encryption state, extracts pages, calculates its exact
   SHA-256, and stores immutable bytes.
2. The browser calls `POST /api/protocols/{id}/analysis`. The API responds `202`
   and runs analysis in a background task; the browser polls
   `GET /api/protocols/{id}/analysis/status` until a terminal state.
3. `GET /api/protocols/{id}/review` exposes source identity, evidence, structure,
   warnings, missing values, readiness blockers, and lifecycle gates.
4. In `demo`, `reference_only`, or `test_only`, a user may explicitly activate a
   guidance-ready revision as a development-only draft. Operational scope never
   permits this shortcut.
5. Approval/revocation history is append-only. Revocation prevents new
   operational sessions but does not erase historical experiment provenance.

For a scanned/text-empty PDF, onboarding pauses before structured analysis. A
reviewer explicitly calls `POST /api/protocols/{id}/ocr`; the server invokes only
the trusted deployment-injected `ProtocolOcrProvider` and validates the exact
source SHA-256, complete ordered page set, bounded text, provider identity,
confidence, language, and warnings. The browser polls `GET
/api/protocols/{id}/ocr`, renders page text with `textContent`, and requires an
accept/reject decision at `POST /api/protocols/{id}/ocr/review`. Acceptance only
makes the reviewed text eligible for a separate structured-analysis request. It
does not start analysis, approve a revision, or make anything executable.

No OCR engine is bundled or selected by a client. A deployment that needs scan
support must inject an adapter as `app.state.protocol_ocr_provider`; without one,
the endpoint returns `protocol_ocr_not_configured` and preserves the immutable
PDF. The adapter contract is defined in
`src/voice_workflow_agent/protocol_ocr.py`. This keeps local binaries, cloud OCR
credentials, and provider choice outside HTTP input and the voice execution
path.

Missing provider configuration is persisted as an actionable failure with retry;
it is not displayed forever as an unexplained `analysis_required` state.
Unsupported conditions, ambiguities, critical missing values, conflicts,
unreviewed/invalid OCR, corrupt/encrypted PDFs, and unsafe files fail closed.

Long text-native onboarding is evidence-first. Documents over eight pages, or
documents that exceed the existing single-pass request envelope, are split into
page-aligned chunks without lowering the 192 KiB per-chunk text ceiling. The
provider returns a small claim DTO rather than one `ExperimentProtocol` per
chunk. Every material, equipment, action, value, prerequisite, hazard,
observation, repeat, or explicit missing-value claim repeats the immutable source
revision, SHA-256, one-based page, and exact contiguous excerpt. Deterministic
validation rejects a whole chunk on any fabricated, stale, non-contiguous, or
out-of-scope evidence; merge requires every planned chunk and complete page
coverage before whole-document consistency validation and final domain assembly.
Chunk calls are serial by default, concurrency two is explicit/experimental, and
the 120-second limit is one total-run deadline rather than a fresh timeout per
batch. No partial-success result is persisted as a review candidate.

## Lab adaptations

A local protocol difference is represented as a new immutable child revision, never
as an edit to an imported original. The adaptation record pins the exact base
and adapted revision IDs and accepts only step-linked equipment differences,
reagent substitutions, lab notes, and troubleshooting tips. Equipment/reagent
changes require explicit before/after values and a rationale.

Every adaptation begins `review_required`, appears in the existing source-review
inbox, uses the existing diff view, and becomes executable only after the
existing reviewer/admin approval event. A development-status source cannot be
approved directly; an explicit lab-adaptation child must be reviewed. Rejected,
revoked, stale, or already adapted revisions fail closed.

The tenant-scoped API is:

- `POST /api/workspace/protocols/{base_revision_id}/adaptations`;
- `GET /api/workspace/protocol-adaptations`; and
- `GET /api/workspace/protocol-adaptations/{adapted_revision_id}`.

Approval and revocation continue through
`POST /api/workspace/reviewer/revisions/{revision_id}/decision`; there is no
parallel approval mechanism.

## Korean STT reliability

The input preference is `AUTO`, `KOREAN`, or `ENGLISH`; the browser defaults to
Korean for this deployment. Korean mode sends `language=ko`, `format=true`, and
bounded scientific/protocol key terms to xAI. The official API documents that
`language` enables formatting; it does not force the model to transcribe in that
language. The response’s detected BCP-47 language is therefore treated as
evidence, not as a guarantee.

When Korean is selected and the detected language or script conflicts with the
preference, transcript admission emits the fixed clarification:

```text
음성 인식 언어가 불확실합니다. 다시 한 번 말씀해 주세요.
```

No workflow mutation is executed from that mismatched transcript. Sanitized
analytics retain only the mismatch classification and timing—not transcript
text. See the [official xAI STT documentation](https://docs.x.ai/developers/model-capabilities/audio/speech-to-text).

## Semantic intent fallback

Researchers code-switch and paraphrase. `타이머 얼마나 남았어?` and
`타임 얼마나 남았어?` are recognized deterministically, but `Time 얼마나 남았어?`
is the same question in a form no regex table anticipated. The semantic intent
fallback answers that class of utterance without giving a model any authority.

```text
STT
 → deterministic intent fast path            (unchanged, still the fast path)
 → semantic intent proposal                  (only when the fast path returns a catch-all)
 → server-owned policy validation            (evidence, context, and tier gates)
 → deterministic workflow state machine      (the only thing that transitions)
 → persistence
 → acknowledgement
```

The resolver may propose only an intent that already exists in the curated
action contract - current step, next-step information, complete current step,
not done, start timer, timer status, timer-operation information, pause, resume,
stop, repeat, related question, or `unknown` - and returns structured data (`intent`, `target`,
`mutation_requested`, `confidence`, `explicit_action_evidence`, `reason`), never
free-form instructions. It uses the same xAI chat boundary as the rest of the
product; no second provider is introduced.

Mutation safety is structural, not advisory:

- **Read-only intents** need only the read-only confidence floor. A timer
  question is answered from the server's own timer, so it can report "the timer
  is not started yet" but can never invent a timer the approved step does not
  define.
- **Bounded control** (start timer, pause, resume) additionally requires
  `mutation_requested`, a verbatim action span copied from the utterance, a
  actual action request rather than an informational or hypothetical question,
  an active workflow, no open confirmation gate, and the higher mutation
  confidence floor. A polite request may end in question punctuation; it still
  passes the same verbatim-evidence and server-state gates.
- **Checkpoint intents** never execute. A completion proposal is downgraded to
  the existing explicit completion confirmation, so the researcher's own answer
  commits the step. A stop proposal is refused outright: ending a run stays a
  deterministically worded command.
- Source-defined observation checkpoints, transcript-quality blocks, pending
  confirmation gates, and the deterministic non-mutating completion guards
  (hypothetical, quoted, negated, future completion) are all evaluated
  independently of the proposal and continue to win.

Failure is always closed. If the fallback is disabled, the resolver is
unreachable, the call times out, the structured output is malformed, the
proposed intent is unsupported, or confidence is below the floor, the turn keeps
exactly the outcome the deterministic path already produced. A turn the
deterministic path resolves never constructs a provider client at all, so voice
interaction never depends on the model being available.

Enable it per deployment (see `.env.example`):

```bash
VOICE_WORKFLOW_AGENT_SEMANTIC_INTENT_ENABLED=true
VOICE_WORKFLOW_AGENT_SEMANTIC_INTENT_MODEL=grok-4.20-0309-non-reasoning
VOICE_WORKFLOW_AGENT_SEMANTIC_INTENT_TIMEOUT_SECONDS=2.5
VOICE_WORKFLOW_AGENT_SEMANTIC_INTENT_MIN_CONFIDENCE=0.6
VOICE_WORKFLOW_AGENT_SEMANTIC_INTENT_MUTATION_MIN_CONFIDENCE=0.85
```

The dedicated non-reasoning model keeps this small typed classification off the
slower general reasoning path. The 2.5-second value is a hard provider boundary,
not permission to retry; the request uses no tools and caps its output at 160
tokens.

Every turn publishes a privacy-safe ruling on `turn.route_decision` under
`semantic_fallback` (`status`, `reason_code`, `proposed_intent`, `accepted`,
`confidence`, `latency_ms`) - reason codes and enum values only, never
utterance text or model prose.

## Workspace identity and authorization

Workspace mode models organizations, principals, roles, memberships, ownership,
and tenant-scoped resources. Roles are `researcher`, `reviewer`, `lab_admin`, and
`organization_admin`; permissions are enforced centrally.

OIDC bearer tokens require signed `RS256` or `ES256` JWTs with matching issuer and
audience plus `exp`, `iat`, `iss`, `aud`, and `sub`. The tenant and roles come from
server-configured claims. Effective permissions are the intersection of verified
OIDC roles and active local memberships. External subjects are represented by an
opaque issuer-scoped principal ID.

The boundary is provider-neutral OpenID Connect and is compatible with Google
Workspace, Microsoft Entra ID, Auth0, and Keycloak when each issuer supplies an
HTTPS JWKS endpoint and the configured tenant/role claims. Provider setup and
claim mapping remain deployment responsibilities; the application does not add
provider-specific token shortcuts.

An allowlisted development identity provider is available only outside
`operational` scope. Operational workspace access requires a complete OIDC
configuration. Client-supplied tenant IDs are never accepted as an ownership
override. HTTP and WebSocket access share the same identity boundary.

## Protocol Source Hub

All imports produce an immutable `ProtocolSource` and a new lineage revision when
the source identity changes.

### protocols.io

- Accepts an exact DOI, protocol URL, URI, or version-qualified `/vN` identity.
- Calls `GET /api/v4/protocols/{id}` with a server-side bearer token and requests
  structured Markdown content.
- Preserves DOI, version URI, authors, license, source status, material/step
  structure, warnings, and canonical URL.
- Never upgrades an “In development” source to approved.

Official contract: [protocols.io API](https://apidoc.protocols.io/).

### Google Drive and Shared Drives

- Read-only folder allowlists; supports PDFs and Google Docs exported as PDF.
- Preserves file ID, modified timestamp, head revision where available, parents,
  owner metadata allowed by Drive, and Shared Drive identity.
- Uses `supportsAllDrives`, `includeItemsFromAllDrives`, and the Drive change-log
  cursor. The next cursor is persisted per connector/root.
- A changed file becomes a review-required revision; active revisions are never
  overwritten.

Official contracts: [Drive files](https://developers.google.com/workspace/drive/api/reference/rest/v3/files)
and [Drive changes](https://developers.google.com/workspace/drive/api/reference/rest/v3/changes/list).

### GitHub

- Read-only repository/ref/path allowlists; source content is pinned to the
  resolved commit SHA.
- Preserves repository, branch/tag, commit, path, license, and source URL.
- Webhooks verify `X-Hub-Signature-256` over the raw body with HMAC-SHA256 and a
  constant-time comparison, enforce delivery replay protection, and import only
  changed allowlisted paths.
- Imported repository content is never executed by the FastAPI process.

Official contract: [GitHub webhook validation](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries).

## Dry-lab workflow registry

Snakemake and Nextflow imports are metadata-only. The registry recognizes exact
entry points, configuration/schema/environment files, declared rules or
processes, engine metadata, repository identity, and commit. Reviewer decisions
are append-only. An explicit link connects a real tenant-owned durable
ExperimentSession, its matching source-hash-bound wet-lab lineage revision, and
one approved computational workflow revision. The researcher cockpit shows that
link with its repository, commit, and entry point; the API always reports
`execution_supported:false` and `execution_started:false`.

There is no arbitrary workflow execution, validation sandbox, or Seqera launch in
this service. `SeqeraLaunchBoundary` is an integration interface for a future
separate execution plane. The registry follows the repository-structure patterns
documented by the [Snakemake Workflow Catalog](https://snakemake.github.io/snakemake-workflow-catalog/docs/snakemake.html)
and [Nextflow](https://docs.seqera.io/nextflow/workflow).

New links fail closed when the experiment is only a fabricated resource binding,
the wet-lab lineage protocol/source identity differs, the runtime revision is
incompatible with the catalog revision, the workflow is unreviewed/revoked, or
the stored metadata claims execution support. Revocation is terminal for that
immutable workflow revision; a changed workflow must be imported and reviewed as
a new revision.

## Knowledge, translations, and asset cards

The workspace store separates `ApprovedProtocolFact`, `LabTip`,
`HistoricalObservation`, and `TroubleshootingNote`. Observations and tips remain
non-authoritative until a reviewer explicitly promotes them into an approved
annotation; provenance is retained.

Translations are linked to the original revision, labeled machine-generated or
reviewed, and rejected if protected scientific numeric tokens differ from the
source. Lightweight reagent/equipment cards store tenant-scoped location,
optional photo/QR/barcode metadata, and an HTTPS SDS/source link. Location changes
produce a reviewable history rather than a hidden overwrite.

## eLabFTW write-back

`ElnConnector` is the generic boundary; `ELabFtwConnector` implements the real
eLabFTW API v2 create-then-patch contract. A write-back requires:

- a completed, tenant-owned durable ExperimentSession and its completed report;
- exact agreement between the session protocol/revision and report
  protocol/revision identities;
- the exact tenant-owned protocol lineage revision and matching source/execution
  identity;
- an enabled eLabFTW connector with an allowlisted HTTPS origin;
- explicit user confirmation; and
- a unique idempotency key reserved before the network write.

The server builds the payload from its own report store. Raw audio, full
transcripts, model reasoning, and secrets are never sent. Unpublished protocol
instructions are withheld by default. Cross-origin `Location` responses are
rejected before PATCH to prevent follow-up SSRF. See the
[eLabFTW API v2 documentation](https://doc.elabftw.net/api/v2/).

## Setup

Python 3.12 or newer is required.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
cp .env.example .env
```

Create the approved safety catalog and choose absolute, ignored runtime data
directories. Configure `.env`, then start:

```bash
uvicorn voice_workflow_agent.server:app --host 127.0.0.1 --port 8000
```

The optional safety-handoff worker remains separate from the low-latency voice
loop:

```bash
python -m voice_workflow_agent.worker
```

### Core configuration

| Variable | Purpose |
|---|---|
| `XAI_API_KEY` | Server-only xAI credential |
| `CHAT_MODEL`, `WORKER_MODEL` | Agent and handoff-worker models |
| `PROTOCOL_ANALYSIS_MODEL` | Required structured protocol analysis model; current deployment example: `grok-4.6` |
| `PROTOCOL_ANALYSIS_REASONING_EFFORT` | Protocol-analysis reasoning effort; defaults to compatibility-preserving `high` |
| `TTS_VOICE` | Cascade voice; defaults to `leo` |
| `VOICE_WORKFLOW_AGENT_USAGE_SCOPE` | `operational`, `demo`, `reference_only`, or `test_only` |
| `VOICE_WORKFLOW_AGENT_SAFETY_CATALOG` | Absolute approved safety-catalog path |
| `VOICE_WORKFLOW_AGENT_PROTOCOL_ENABLED` | Enables immutable PDF catalog |
| `VOICE_WORKFLOW_AGENT_PROTOCOL_DATA_DIR` | Absolute ignored protocol-store directory |
| `VOICE_WORKFLOW_AGENT_PROTOCOL_CLAIM_CHUNKS_ENABLED` | Default-off gate for controlled evidence-first claim-chunk evaluation |
| `VOICE_WORKFLOW_AGENT_WORKSPACE_ENABLED` | Enables tenant/RBAC/source workspace |
| `VOICE_WORKFLOW_AGENT_WORKSPACE_DATA_DIR` | Absolute ignored workspace directory |
| `VOICE_WORKFLOW_AGENT_ANALYTICS_RETENTION_DAYS` | Tenant default, 1–3650 days |
| `VOICE_WORKFLOW_AGENT_EXPERIMENT_REPORTS_ENABLED` | Enables append-only experiment records |
| `VOICE_WORKFLOW_AGENT_EXPERIMENT_REPORT_DB` | Absolute report SQLite path |

`PROTOCOL_ANALYSIS_MODEL` is read from deployment environment configuration;
there is no hidden model fallback. Protocol analysis uses `grok-4.6` in the
current deployment example. Protocol analysis explicitly defaults to high
reasoning effort. Lower effort levels remain deployment-configurable but must
pass protocol-specific completeness and evidence validation before use. The
separate low-latency semantic-intent path keeps its dedicated non-reasoning
model and timeout settings.

### Identity configuration

For operational workspace mode, configure all of:

```dotenv
VOICE_WORKFLOW_AGENT_OIDC_ISSUER=https://id.example.test/
VOICE_WORKFLOW_AGENT_OIDC_AUDIENCE=voice-workflow-agent
VOICE_WORKFLOW_AGENT_OIDC_JWKS_URL=https://id.example.test/.well-known/jwks.json
VOICE_WORKFLOW_AGENT_OIDC_TENANT_CLAIM=organization_id
VOICE_WORKFLOW_AGENT_OIDC_ROLES_CLAIM=roles
VOICE_WORKFLOW_AGENT_OIDC_NAME_CLAIM=name
```

For a non-operational local demo, omit OIDC values and optionally set a JSON
allowlist in `VOICE_WORKFLOW_AGENT_DEV_AUTH_PROFILES`. If omitted, one local
lab-admin profile is created. Do not enable development identities in operational
scope.

### Connector secrets

Connector records contain opaque `secret://` references, never credential values.
The application resolves them through a server-owned environment mapping:

```dotenv
VOICE_WORKFLOW_AGENT_SECRET_REFERENCES={"secret://tenant-a/protocols-io":"PROTOCOLS_IO_TOKEN","secret://tenant-a/drive":"DRIVE_ACCESS_TOKEN","secret://tenant-a/github":"GITHUB_INSTALLATION_TOKEN","secret://tenant-a/github-webhook":"GITHUB_WEBHOOK_SECRET","secret://tenant-a/elabftw":"ELABFTW_API_KEY"}
```

Set the referenced environment variables only in the process secret manager.
Connector `allowed_roots` constrain Drive folders/shared drives, GitHub
repository/ref/path, or an eLabFTW HTTPS origin. Live OAuth/App provisioning is an
operator responsibility; local tests use fakes.

## API surface

The browser consumes these main groups:

- `/api/protocols`: local upload, lifecycle, analysis status, evidence review,
  development activation, approval, source pages, and verified assets;
- `/api/workspace/session` and `/protocol-library`: identity-aware workspace and
  quick protocol access;
- `/api/workspace/experiments` and `/experiments/{session_id}`: tenant-owned
  experiment dashboard, recovery version, completed steps, observations,
  opaque evidence metadata, reviewer actions, and lifecycle timeline;
- `/api/workspace/protocol-adaptations`: immutable, typed lab-adaptation drafts
  linked to an exact original and the existing reviewer approval path;
- `/api/workspace/reviewer/*`: source inbox, diff, decisions, translations,
  knowledge promotion, and dry-lab review;
- `/api/workspace/admin/*`: memberships, connector configuration/check/enable,
  retention, asset cards, audit posture, tenant analytics, and pilot metrics;
- `/api/workspace/sources/*`: protocols.io, Drive, GitHub, and dry-lab import;
- `/api/workspace/webhooks/github/{connector_id}`: signed, replay-protected source
  updates;
- `/api/workspace/eln/elabftw/writeback`: confirmed experiment export; and
- `/api/experiment-reports/*`: tenant-scoped report reads/exports.

For deployment probes, `GET /healthz` is a pure liveness check (the process
can serve a request); `GET /readyz` validates identity, workspace,
protocol-catalog, and report configuration and returns the non-secret identity
mode plus capability flags (`workspace_enabled`, `protocol_catalog_enabled`,
`experiment_reports_enabled`, `moss_enabled`). Optional external providers do
not block readiness, and a `503` means local configuration failed to parse—not
that a live external call was attempted.

All sensitive workspace APIs derive the tenant from the authenticated principal.
Connector list responses never return credential references or resolved secrets.

## Replay and voice evaluation

The A–G replay no longer relies on an ad-hoc `PYTHONPATH`:

```bash
voice-workflow-replay
# Equivalent project-native invocation:
python -m voice_workflow_agent.replay_turns
# The historical script remains a thin compatibility wrapper:
python scripts/replay_turns.py
```

Evaluate a sanitized JSON manifest of recognized/reference outcomes without
loading audio:

```bash
voice-workflow-evaluate path/to/results.json
```

The manifest reports WER, semantic and command accuracy, false mutation rate,
VAD error, endpoint/barge-in latency, and repeat/correction rate. Field recordings
require an explicit consent ID and bounded retention. See
[`docs/VOICE_FIELD_EVALUATION_PLAN.md`](docs/VOICE_FIELD_EVALUATION_PLAN.md).

## Verification

Running the suite requires the `test` extra (`pip install -e '.[test]'`, which
adds `pytest` and `httpx`):

```bash
python -m pip install -e '.[test]'
python -m pytest -q
python -m compileall -q src tests scripts
git diff --check
```

Browser acceptance coverage for the researcher/reviewer/admin workspaces (desktop
and mobile viewports) lives under `tests/e2e/` and runs separately via
[Playwright](https://playwright.dev/):

```bash
npm install
npx playwright install --with-deps chromium
npx playwright test
```

GitHub Actions (`.github/workflows/ci.yml`) runs both on every push/PR to
`main` and `refactor/**`. Its browser job uses `playwright.ci.config.ts` with
`scripts/run_ci_server.sh`, a credential-free server launcher that runs with
an empty protocol catalog instead of the full Candidate A demo fixture, since
that fixture's integrity check requires an externally licensed source PDF
that is intentionally not committed to the repository. Local development
still uses `scripts/run_candidate_a.sh` (the default `playwright.config.ts`)
for full-fidelity manual testing when that PDF is available.

The same externally licensed PDF also backs 14 pytest modules' byte-exact
source-identity checks and both `scripts/evaluate_candidate_a_*.py`
evaluators. `tests/conftest.py` skips those modules (with an explicit reason)
whenever the PDF is absent, and the CI workflow does the same for the
evaluator scripts, rather than faking the file or hiding a real failure
behind it. Everything else - the full non-PDF-dependent test suite, the
Playwright browser suite, and `python scripts/replay_turns.py` - runs
identically in CI and locally.

Tests are provider-free unless explicitly marked otherwise. Connector and
eLabFTW contracts use fakes; the real adapters remain in the production code
path. The current integration classification and exact historical live-test
evidence are in
[`docs/COMMERCIALIZATION_PASS4_REPORT.md`](docs/COMMERCIALIZATION_PASS4_REPORT.md)
and the current [`Capability Matrix`](docs/CAPABILITY_MATRIX.md).

## Security and privacy boundaries

- Immutable source hashes, exact revisions, tenant bindings, central RBAC, and
  negative IDOR tests protect protocol/report/asset ownership.
- OIDC tokens are signature/issuer/audience/time validated; production does not
  fall back to a shared admin token or development identity.
- PDFs and connector documents have byte limits, strict identifiers, sanitized
  filenames, and no executable path.
- protocols.io and GitHub identifiers reject alternate origins, credentials,
  traversal, and unallowlisted roots. eLabFTW and displayed web assets enforce
  HTTPS/same-origin or SSRF controls.
- GitHub webhooks verify the raw payload before normal workspace middleware and
  fence repeated delivery IDs.
- Approval and write-back idempotency keys are append-only replay fences.
- Analytics persist allowlisted categories/dimensions only and purge according to
  tenant retention policy.
- Pilot metrics expose only tenant-scoped counts; durable counts and
  retention-bounded analytics are labeled separately.
- Evidence downloads are tenant-authorized and verified against their recorded
  byte size and SHA-256 before delivery.
- Audio diagnostics are disabled by default, bounded when enabled, and must stay
  in an ignored runtime directory.

This is not a claim of electronic-signature, GLP/GMP, HIPAA, or other regulatory
compliance. A controlled deployment still requires an IdP, secrets manager,
encrypted backup/storage policy, facility-specific approval, validation evidence,
and user-accessibility/noisy-lab studies.

## Known limitations and deliberate non-goals

- No autonomous protocol approval or safety decision.
- No full ELN/LIMS, inventory, video hosting, or facility directory.
- No arbitrary GitHub/Snakemake/Nextflow execution in the voice server.
- No live Seqera, Google Drive, GitHub App, protocols.io authenticated import, or
  eLabFTW instance verification without operator credentials.
- No claim of field STT performance until the consented noisy-lab evaluation plan
  is executed.
- No cross-process job queue yet: PDF analysis background tasks are process-local;
  persisted lifecycle state and explicit retry make restarts visible and safe.
- SQLite storage and the single-process deployment path are suitable for a
  controlled pilot, not horizontal scaling or automatic failover.
- The browser experience is suitable for a controlled pilot, not a substitute
  for facility operating procedures or emergency systems; noisy-lab and
  accessibility field validation remain outstanding.
