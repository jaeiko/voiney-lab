# Voice Workflow Agent

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
`src/voice_workflow_agent/runtime_routing.py`. Tenant/RBAC logic is in
`identity.py` and `workspace_store.py`. Protocol source adapters are in
`protocol_sources.py`; computational metadata is in `drylab_workflows.py`; the
ELN boundary is in `eln_connectors.py`.

The pre-extension component and authority map is documented in
[`docs/ARCHITECTURE_MAP.md`](docs/ARCHITECTURE_MAP.md). Implementation and phase
evidence are tracked in
[`docs/LAB_WORKFLOW_OS_IMPLEMENTATION_REPORT.md`](docs/LAB_WORKFLOW_OS_IMPLEMENTATION_REPORT.md),
with forward-only storage details in
[`docs/MIGRATION_NOTES.md`](docs/MIGRATION_NOTES.md).

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
interpretation in this phase. Internal storage references are not returned by
the API or timeline UI.

Timeline endpoints are:

- `GET /api/workspace/experiments/{session_id}/timeline`;
- `POST /api/workspace/experiments/{session_id}/observations`;
- `POST /api/workspace/experiments/{session_id}/evidence`; and
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

Missing provider configuration is persisted as an actionable failure with retry;
it is not displayed forever as an unexplained `analysis_required` state.
Unsupported conditions, ambiguities, critical missing values, conflicts, scanned
documents that require OCR, corrupt/encrypted PDFs, and unsafe files fail closed.

## Lab adaptations

A local SOP difference is represented as a new immutable child revision, never
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
are append-only. An explicit link can connect an exact wet-lab experiment and
sample/data reference to an approved computational workflow revision.

There is no arbitrary workflow execution, validation sandbox, or Seqera launch in
this service. `SeqeraWorkflowBoundary` is an integration interface for a future
separate execution plane. The registry follows the repository-structure patterns
documented by the [Snakemake Workflow Catalog](https://snakemake.github.io/snakemake-workflow-catalog/docs/snakemake.html)
and [Nextflow](https://docs.seqera.io/nextflow/workflow).

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
| `PROTOCOL_ANALYSIS_MODEL` | Structured protocol analysis model |
| `TTS_VOICE` | Cascade voice; defaults to `leo` |
| `VOICE_WORKFLOW_AGENT_USAGE_SCOPE` | `operational`, `demo`, `reference_only`, or `test_only` |
| `VOICE_WORKFLOW_AGENT_SAFETY_CATALOG` | Absolute approved safety-catalog path |
| `VOICE_WORKFLOW_AGENT_PROTOCOL_ENABLED` | Enables immutable PDF catalog |
| `VOICE_WORKFLOW_AGENT_PROTOCOL_DATA_DIR` | Absolute ignored protocol-store directory |
| `VOICE_WORKFLOW_AGENT_WORKSPACE_ENABLED` | Enables tenant/RBAC/source workspace |
| `VOICE_WORKFLOW_AGENT_WORKSPACE_DATA_DIR` | Absolute ignored workspace directory |
| `VOICE_WORKFLOW_AGENT_ANALYTICS_RETENTION_DAYS` | Tenant default, 1–3650 days |
| `VOICE_WORKFLOW_AGENT_EXPERIMENT_REPORTS_ENABLED` | Enables append-only experiment records |
| `VOICE_WORKFLOW_AGENT_EXPERIMENT_REPORT_DB` | Absolute report SQLite path |

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
- `/api/workspace/protocol-adaptations`: immutable, typed local SOP drafts
  linked to an exact original and the existing reviewer approval path;
- `/api/workspace/reviewer/*`: source inbox, diff, decisions, translations,
  knowledge promotion, and dry-lab review;
- `/api/workspace/admin/*`: memberships, connector configuration, retention,
  asset cards, and tenant analytics;
- `/api/workspace/sources/*`: protocols.io, Drive, GitHub, and dry-lab import;
- `/api/workspace/webhooks/github/{connector_id}`: signed, replay-protected source
  updates;
- `/api/workspace/eln/elabftw/writeback`: confirmed experiment export; and
- `/api/experiment-reports/*`: tenant-scoped report reads/exports.

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

```bash
python -m pytest -q
python -m compileall -q src tests scripts
git diff --check
```

Tests are provider-free unless explicitly marked otherwise. Connector and eLabFTW
contracts use fakes; the real adapters remain in the production code path. The
Pass 2 report records which external systems were actually live-tested:
[`docs/CODEX_COMMERCIALIZATION_PASS2_REPORT.md`](docs/CODEX_COMMERCIALIZATION_PASS2_REPORT.md).

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
- The development UI is suitable for a controlled pilot, not a substitute for
  facility operating procedures or emergency systems.
