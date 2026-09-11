# Voice Workflow Agent Commercialization Pass 2 Report

Date: 2026-08-22

Branch: `refactor/voice-workflow-agent-stability`

Baseline SHA: `51186193cee1d13840b1fbb8c9a71c28c4394b92`

Final feature-implementation SHA: `21c2e1e279488255776dd7ef927f9be99891aa0a`

Final repository handoff SHA: reported in the final handoff message; a Git commit
cannot contain its own final hash without changing that hash

## Executive outcome

Pass 2 turns the controlled voice-workflow baseline into a coherent, tenant-aware
laboratory protocol knowledge and execution layer:

```text
reviewed source → immutable revision → reviewer decision
  → exact voice execution → append-only experiment record → confirmed integration
```

The production Cascade voice path and its deterministic workflow authority were
preserved. The new platform boundary adds trusted source onboarding, lineage,
identity/RBAC, review/revocation, source connectors, dry-lab metadata, knowledge
and asset metadata, controlled eLabFTW write-back, and persistent privacy-safe
analytics. It does not claim autonomous science, autonomous safety, electronic
signature compliance, a full ELN/LIMS, or arbitrary code execution.

Status terms in this report are strict:

- **Implemented**: production code and API/UI boundary exist.
- **Tested with fake**: the real adapter/contract is exercised against a local
  deterministic transport or provider fake.
- **Tested live**: an actual external service or real browser/file path was used.
- **Not yet validated**: credentials, service instance, or field evidence were
  unavailable; no live claim is made.

## Regression-critical voice baseline

Before feature work, the complete offline baseline passed with `680 passed, 679
subtests passed` in 118.82 seconds. New production-path regression tests preserve:

- learning/rationale and protocol-audit questions;
- no-history resume behavior;
- combined “why + next” with explicit completion gates;
- web reference and visual-request routing;
- pause/resume, barge-in, stale-turn cancellation, and playback identity; and
- production WebSocket routing rather than helper-only behavior.

No second voice architecture was introduced. Cascade remains the only active
path.

## Bugs and root causes found

### Replay harness

**Root cause:** `scripts/replay_turns.py` imported a `src`-layout package while
implicitly relying on an editable install or ad-hoc `PYTHONPATH`. A contributor
running the script directly could therefore get `ModuleNotFoundError`.

**Resolution:** replay is now a package module with the installed
`voice-workflow-replay` entry point. `python -m
voice_workflow_agent.replay_turns` is the canonical equivalent; the historical
script is a thin compatibility wrapper.

### Real arbitrary-PDF onboarding

The real acceptance document was obtained through its public DOI:
[Measuring leaf carbon fractions with the ANKOM2000 Fiber Analyzer V.1](https://doi.org/10.17504/protocols.io.yinfude).

Observed acceptance-file facts:

- 48,966,714 bytes;
- 40 pages;
- SHA-256
  `75eeed09e2d21711c7e834b13cf2e410303037b5f9897905dd3aea8dc6fa4eaa`;
- 22,486 extracted text bytes; and
- zero PDF extraction warnings.

The file was below the 64 MiB upload bound and below the analysis request-size
bound. Size, corruption, encryption, OCR, and chunking were therefore not the
cause of the observed stalled state.

**Exact root cause:** the local environment had neither `XAI_API_KEY` nor
`PROTOCOL_ANALYSIS_MODEL`. Structured analysis could not start. The earlier
frontend reduced that provider/configuration failure to a non-actionable
`analysis_required` state; analysis also ran in the request lifecycle rather than
as a visible background job. The source bytes were stored correctly, but failure,
retry, and progress were not exposed coherently.

**Resolution:** registration, analysis request, background processing, persisted
lifecycle/failure state, polling, evidence review, readiness gates, explicit
development activation, approval, and revocation are now distinct. Missing
configuration persists as `provider_configuration_missing` with a Korean action
message naming the two required variables and an explicit retry. A long request
returns `202` and visibly progresses. Analysis success never implies operational
approval.

### Workspace library execution state

The real browser run found that a just-activated local PDF remained marked
non-executable in the workspace lineage cache. The protocol catalog was already
authoritative and executable; the quick library was reading only its independent
approval event projection.

**Resolution:** the protocol-library endpoint now enriches tenant-owned lineage
rows with the authoritative catalog revision, approval/lifecycle state, and
`available_for_execution`. The browser refreshes the quick library after
activation, exposing an exact-revision action.

### Arbitrary-PDF visual request

The extended browser lifecycle found a second real defect: an arbitrary-PDF
visual request called `ProtocolKnowledgeView.from_fixture`, which assumed the
Candidate A page-2 headings `Abstract`, `Protocol materials`, `Safety warnings`,
and `Before start`. The ANKOM-derived draft did not have that layout, so the
production turn raised `CuratedProtocolFixtureError`.

**Resolution:** protocol-wide knowledge now derives a safe generic purpose,
warnings, and section projection from structured source evidence when the curated
Candidate A layout is absent. Candidate A–specific explanatory claims are gated
to Candidate A so an arbitrary PDF with a coincidentally similar reagent name
cannot inherit the wrong authority. A production-runtime regression verifies that
an arbitrary PDF visual request remains read-only and does not crash.

### Korean STT

The provider behavior was not a conventional translation setting bug. Current
[xAI STT documentation](https://docs.x.ai/developers/model-capabilities/audio/speech-to-text)
states that `language` enables formatting/Inverse Text Normalization; the model
transcribes supported speech regardless of that parameter. The response separately
returns a detected BCP-47 language. Therefore `language=ko` cannot be treated as a
promise that Korean speech will never produce conflicting English/Latin output.

**Prior failure:** a plausible English transcript could pass downstream intent
classification and mutate workflow state even though the researcher spoke Korean.

**Resolution:** the session exposes `AUTO`, `KOREAN`, and `ENGLISH`. Korean mode
sends documented formatting and bounded scientific key terms, then applies a
server-side language/script consistency gate. Conflicting or obviously translated
text receives exactly:

> 음성 인식 언어가 불확실합니다. 다시 한 번 말씀해 주세요.

That turn cannot complete, stop, resume, confirm, select, or record an observation.
Only sanitized detected-language/mismatch diagnostics are retained.

## Implemented platform capabilities

### Protocol source architecture and lineage

`ProtocolSourceHub` normalizes local PDF, protocols.io, Drive, and GitHub sources
into immutable version identities and hashes. The tenant store implements
`ProtocolFamily`, `ProtocolSource`, lineage revisions, parent relationships,
translations, append-only approvals, source inboxes, reviewable diffs, and resource
bindings. A source change creates a new draft instead of overwriting an active
revision. Existing sessions/reports remain pinned to the revision they started
with.

Local PDF registration additionally records the exact PDF SHA and catalog
execution identity in the source lineage. Translations remain subordinate to the
original revision; protected numeric/scientific token changes fail validation.

### protocols.io integration

The adapter accepts DOI, protocols.io URL, URI, or exact `/vN` identity and calls
the current `GET /api/v4/protocols/{id}` contract with `content_format=markdown`.
It preserves exact version URI, DOI, authors, license, published/development
status, materials, steps, warnings, canonical URL, and source identity. “In
development” remains a non-approvable source gate.

The official API requires a bearer token even for public protocol reads, as shown
in the [protocols.io API documentation](https://apidoc.protocols.io/). A live
unauthenticated ANKOM API request returned HTTP 400 / service status 1218, which
confirmed this boundary; authenticated import was tested with a contract fake and
is not claimed live.

### Google Drive / Shared Drive integration

The real read-only adapter:

- allowlists folder IDs and optional Shared Drive IDs;
- uses Drive v3 `files.list` with `supportsAllDrives` and
  `includeItemsFromAllDrives`;
- downloads PDFs and exports Google Docs as PDF;
- preserves file, parent, modified-time, owner, head-revision, and drive metadata;
- obtains and persists per-connector/root change cursors; and
- imports a changed file as a new review-required revision.

This follows the official [Drive files](https://developers.google.com/workspace/drive/api/reference/rest/v3/files)
and [change-log](https://developers.google.com/workspace/drive/api/reference/rest/v3/changes/list)
contracts. Request/query/cursor semantics are tested with fakes. No Google OAuth
credential or Shared Drive was available for live verification.

### GitHub integration

The read-only GitHub adapter allowlists repository, ref, and path prefix; resolves
the requested ref to a commit; and reads content pinned to that commit. Repository,
commit SHA, ref, path, license, content SHA, and canonical URL are preserved.

The webhook endpoint validates HMAC-SHA256 over the exact raw body using
`X-Hub-Signature-256` and a constant-time comparison, matching
[GitHub’s official validation guidance](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries).
It applies a body bound, tenant connector/root checks, and an append-only delivery
replay fence. A push imports only changed allowlisted paths as new revisions.
Official signature vectors, tamper, replay, and revision semantics are tested with
fakes. No GitHub App installation was live-tested.

### Dry-lab model

Snakemake and Nextflow imports recognize safe metadata: entry point, repository,
commit, path, engine/version indicators, rules/processes, and configuration,
schema, or environment files. Reviewer decisions are append-only. Exact wet-lab
experiment/sample/data identities may be linked to an approved computational
workflow revision.

Imported code is never executed in the FastAPI process. No sandbox runner is
included. A narrow Seqera interface is present only as a future integration
boundary. The metadata model follows public structures documented by the
[Snakemake Workflow Catalog](https://snakemake.github.io/snakemake-workflow-catalog/docs/snakemake.html)
and [Nextflow/Seqera](https://docs.seqera.io/nextflow/workflow).

### Reviewer lifecycle

Roles are `researcher`, `reviewer`, `lab_admin`, and `organization_admin`.
Approval/rejection/revocation records include tenant, actor, role, exact revision,
time, comment/reason, and idempotency key. They are never updated in place.
Revocation denies new operational sessions while preserving historical report
provenance. Reviewer APIs/UI include source inbox, diff, approve, reject, revoke,
translations, knowledge promotion, and dry-lab decisions.

This is an audit trail and future electronic-signature boundary—not a claim of
regulatory electronic-signature compliance.

### Tenant, OIDC, and RBAC model

Sensitive protocol sources/revisions/approvals, sessions, reports, analytics,
connectors, assets, generated/web visuals, dry-lab workflows, and write-backs are
tenant-bound. Central permissions gate each action. Negative IDOR tests cover
cross-tenant revisions, reports, catalog resources, and connector access.

OIDC verification accepts only `RS256`/`ES256`, validates signature, issuer,
audience, expiry/issued-at and required subject claims, and maps the issuer+subject
to an opaque principal. Effective roles are the intersection of verified claims
and active local membership. The allowlisted development identity provider is
available only in non-operational scope; operational workspace access requires a
complete OIDC setup.

### Lab knowledge, asset cards, and bilingual view

Approved facts, lab tips, historical observations, and troubleshooting notes are
separate classes. Historical observations do not become instructions; a reviewer
may explicitly promote a note into an approved annotation with provenance.

Tenant-scoped reagent/equipment cards carry building/room/storage location,
optional photo/QR/barcode metadata, and an HTTPS SDS/source link. Changes produce
a reviewable diff. The active bench view continues to show concise Korean guidance
alongside original source instructions/page identity. Machine translations remain
labeled until reviewed and cannot alter scientific numeric values.

### ELN write-back

`ElnConnector` is the generic interface. `ELabFtwConnector` implements eLabFTW API
v2 `POST /experiments`, reads the same-origin `Location`, then PATCHes the created
experiment, following the [official eLabFTW API](https://doc.elabftw.net/api/v2/).

The HTTP boundary requires explicit `confirmed: true`, an exact completed
tenant-owned server report, a matching protocol lineage/source execution identity,
an enabled connector, and a pre-network idempotency reservation. The payload is
built from server records. Raw audio, unrestricted transcripts, reasoning, and
secrets are excluded; unpublished instructions are withheld. Cross-origin
`Location` is rejected as SSRF. The real adapter is contract-tested with fakes; no
live eLabFTW instance was available.

### Persistent privacy-safe analytics

Tenant SQLite storage records allowlisted aggregate/event categories for voice,
routing, workflow, protocol lifecycle, connectors, reviewer decisions, and
write-back. It excludes audio, unrestricted text, prompts/reasoning, and secrets.
Tenant retention is configurable from 1 to 3650 days and opportunistic purge is
applied. The legacy metrics endpoint delegates to tenant analytics in workspace
mode; the shared-token path remains only for legacy non-workspace deployments.

### Product experience

The single responsive application separates researcher, reviewer, and lab-admin
workspaces. Researcher quick access supports search, favorites, recent ordering,
tags, source, owner/team, version, status, risk, and exact executable revision
selection. Reviewer and admin views expose their controls without crowding the
bench workflow. eLabFTW write-back requires a visible connector/revision choice
and confirmation checkbox.

## Market and product research decisions

Current product patterns were reviewed from official/public sources including
[LabVoice](https://www.labvoice.ai/), [LabTwin](https://www.labtwin.com/),
[Benchling](https://www.benchling.com/), [Scispot](https://www.scispot.com/),
[protocols.io](https://www.protocols.io/developers), eLabFTW, the Snakemake
Workflow Catalog, and Nextflow/Seqera.

The decision was not to imitate a broad lab operating system or rebuild an ELN.
The defensible wedge remains source authority plus hands-free execution:

- connector sources are read-only by default;
- lineage/review precede execution;
- non-SOP knowledge is visibly separate;
- downstream systems receive a controlled completed record; and
- computational workflows are linked metadata until a separate validated
  execution plane exists.

This also matches common academic practice: protocols and SOP-like documents live
across shared folders, protocols.io, and GitHub, while analysis workflows use
repository-native Snakemake/Nextflow layouts. The product meets labs where those
sources already live instead of requiring an immediate platform migration.

## Browser verification

Browser: Google Chrome 151.0.7922.173, headless DevTools protocol.

Desktop viewport: 1440 × 1100.

Mobile viewport: 390 × 844.

The final run used the production FastAPI app, production static UI, production
WebSocket, real audio framing/VAD, production intent router, workflow state
machine, report store, and the real 48.97 MB/40-page ANKOM PDF. Because xAI
credentials were absent, only STT/TTS and structured-model output were replaced by
temporary process-local fakes outside the repository. The analysis fake returned
a minimal source-evidenced one-step draft; it was not a claim that the full ANKOM
protocol had been safely analyzed.

Verified lifecycle:

1. local development identity loaded;
2. real ANKOM PDF uploaded and exact SHA/page count displayed;
3. background analysis moved to `review_required`;
4. evidence/source hash and readiness review rendered;
5. explicit development-only activation produced `executable_draft`;
6. quick library showed the protocols.io-derived title and exact-revision action;
7. session started on the exact catalog revision;
8. four real WebSocket/VAD turns routed as start, learning, visual request, and
   completion;
9. the visual request safely displayed “no verified source visual” rather than
   fabricating an image;
10. completion mutated through the server’s explicit completion path and the
    report reached `completed` with four append-only events;
11. reviewer view exposed protocols.io, Drive, GitHub, and dry-lab controls;
12. admin view exposed connectors, memberships, retention, and analytics; and
13. the 390 px mobile layout had no horizontal overflow.

This run directly discovered and verified the fixes for stale quick-library state
and the arbitrary-PDF visual crash. Google Drive/protocols.io/GitHub UI forms were
verified in-browser; their network adapters remained fake-tested due to missing
credentials.

## External-service validation matrix

| Capability | Implemented | Fake/contract tested | Live tested | Remaining validation |
|---|---:|---:|---:|---|
| Real ANKOM PDF download/extraction/browser upload | yes | yes | yes | full provider analysis |
| xAI STT/Korean formatting/keyterms | yes | yes | no | real Korean microphone/noise matrix |
| xAI structured PDF analysis | yes | yes | no | bounded ANKOM analysis with funded model |
| xAI TTS | existing/preserved | yes | no in Pass 2 | live voice regression |
| protocols.io v4 | yes | yes | auth boundary only | authenticated exact-version import |
| Google Drive / Shared Drive | yes | yes | no | OAuth folder/change-cursor pilot |
| GitHub source/webhook | yes | yes | no | GitHub App + real delivery |
| eLabFTW v2 write-back | yes | yes | no | sandbox instance create/PATCH |
| OIDC | yes | signed-token tests | no | real IdP/JWKS/membership pilot |
| Snakemake/Nextflow metadata | yes | yes | no external service required | representative lab repositories |
| Seqera | interface only | boundary tests | no | separate execution-plane design |

No API key or credential value was printed or committed.

## Automated verification

Pre-change baseline:

```text
680 passed, 679 subtests passed in 118.82s
```

Pass 2 focused integration run before final closeout:

```text
87 passed, 13 subtests passed in 39.73s
```

Final closeout verification:

```text
734 passed, 683 subtests passed in 129.60s
compileall: passed
git diff --check: passed
console, module, and compatibility-script replay paths: passed
```

Core required commands are:

```bash
python -m pytest -q
python -m compileall -q src tests scripts
git diff --check
```

## Deliberately not implemented

- autonomous protocol approval, work-resume authorization, or safety decisions;
- full ELN/LIMS/inventory/facility-directory/video-hosting functionality;
- arbitrary GitHub, Snakemake, or Nextflow code execution;
- a workflow sandbox or live Seqera launcher;
- automatic promotion of observations/tips into approved instructions;
- fake electronic-signature, GLP/GMP, clinical, or regulatory compliance;
- raw-audio persistence by default or fabricated noisy-lab performance claims;
- silent operational promotion of an “In development” source; or
- automatic eLabFTW write-back without explicit confirmation.

## Remaining blockers and controlled-pilot plan

### Blocking manual validation

1. Configure a real xAI key/model and run a bounded full ANKOM structured analysis;
   review all concentrated-acid, hot-equipment, timer, conditional, and missing
   value outputs. Keep it development/reference-only unless the facility approves
   a lab adaptation.
2. Run real Korean microphone trials across the documented noise/mask/distance
   matrix. Measure WER, command accuracy, false mutation, endpoint latency,
   barge-in, and corrections; do not infer field performance from unit tests.
3. Provision a test IdP and validate issuer/JWKS rotation, tenant claims, group/role
   mapping, suspension, and WebSocket reauthentication behavior.
4. Provision one read-only protocols.io token, one Drive/Shared Drive folder, and
   one GitHub App installation/webhook. Verify change cursors/delivery replay and
   review-required revision creation with real updates.
5. Provision a sandbox eLabFTW instance and verify create/PATCH, access controls,
   idempotent retry after network ambiguity, and administrator-visible audit.
6. Validate encrypted storage/backups, restore, secret rotation, data deletion,
   retention jobs, logging/export, accessibility, and facility incident response.

### Recommended pilot sequence

1. Choose 5–20 researchers and one repetitive, low-hazard, facility-approved
   protocol; do not use ANKOM concentrated-acid operations as the first operational
   workflow.
2. Configure OIDC, tenant memberships, secret manager, encrypted runtime storage,
   retention, backup/restore, and one source connector.
3. Import the source, create a lab adaptation, review the diff/evidence/hazards,
   approve one exact revision, and freeze the acceptance evidence.
4. Run scripted and real-voice acceptance, including interruption, language
   mismatch, offline/provider failure, revocation, report export, and eLabFTW
   sandbox write-back.
5. Measure time-to-first-executable-protocol, command accuracy, false mutation,
   completion/documentation rate, corrections, blocked steps, and write-back
   success without storing raw audio by default.
6. Review results with lab safety, IT/security, and protocol owners before
   expanding protocols, tenants, or integrations.

### Engineering follow-ups

- Move process-local PDF analysis tasks to a durable worker/job queue for
  multi-process deployment. Persisted state and explicit retry are safe today, but
  work does not survive a process restart automatically.
- Add OCR as a separately reviewed ingestion path.
- Add production secrets-manager adapters rather than the current environment
  reference resolver.
- Add formal migration/version tooling before changing workspace schema v1.
- Add a separately isolated, resource-limited workflow validation/execution plane
  only if pilot demand justifies it.

## Final assessment

The code now supports the intended evolution from “voice assistant for one
protocol” to a trusted laboratory protocol knowledge and execution layer. The
remaining risks are predominantly real-provider, enterprise infrastructure,
facility validation, and field-performance work—not missing claims hidden behind
demo fixtures. Human review and deterministic server authority remain the core
commercialization boundary.
