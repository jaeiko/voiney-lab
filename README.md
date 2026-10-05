# Voice Workflow Agent

[![CI](https://github.com/jaeiko/voiney-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/jaeiko/voiney-lab/actions/workflows/ci.yml)

Canonical repository: [`jaeiko/voiney-lab`](https://github.com/jaeiko/voiney-lab).
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
`src/voiney_lab/runtime_routing.py`. An optional, disabled-by-default
semantic intent fallback (`semantic_intent.py`) sits behind it: when
deterministic routing returns a catch-all, it may *propose* one of the existing
bounded workflow actions, and server-owned policy in the same boundary decides
whether that proposal is used. It never mutates workflow state - see
[Semantic intent fallback](#semantic-intent-fallback). An LLM router that
takes over intent judgment behind deterministic front rules is wired behind a
setting that is off by default - see
[LLM router (off by default)](#llm-router-off-by-default).
Tenant/RBAC logic is in
`identity.py` and `workspace_store.py`. Protocol source adapters are in
`protocol_sources.py`; computational metadata is in `drylab_workflows.py`; the
ELN boundary is in `eln_connectors.py`.

The current component, authority, and persistence design is documented in
[`docs/CURRENT_ARCHITECTURE.md`](docs/CURRENT_ARCHITECTURE.md). The older
[`docs/ARCHITECTURE_MAP.md`](docs/ARCHITECTURE_MAP.md) is a labeled
pre-extension snapshot. Current capabilities and documentation authority are
indexed in [`docs/DOCUMENTATION_INDEX.md`](docs/DOCUMENTATION_INDEX.md).
Forward-only storage details are in
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

A readable PDF with only some pages marked `ocr_required` does not pause: its
readable pages are analysed as usual, and the same OCR route
(`GET`/`POST /api/protocols/{id}/ocr`, then review) is open for the marked
pages. Until a reviewer has accepted their OCR text and the Protocol has been
analysed again from it, readiness carries `source_page_requires_ocr` naming
those pages, so the Protocol is not guidance-ready. No gate acknowledgement
clears it.

No OCR engine is selected by a client. At startup the server builds the
adapter from the environment (`src/voiney_lab/protocol_ocr_providers.py`) and
injects it as `app.state.protocol_ocr_provider`; an adapter a deployment has
already injected is kept. Two engines are supported, both over REST with
`requests`:

| `VOINEY_LAB_OCR_PROVIDERS` entry | Engine | Credentials |
| --- | --- | --- |
| `clova` | NAVER CLOVA OCR (General, V2), Korean print and handwriting | `VOINEY_LAB_CLOVA_OCR_INVOKE_URL` (https), `VOINEY_LAB_CLOVA_OCR_SECRET` (sent as `X-OCR-SECRET`) |
| `google` | Google Cloud Vision `DOCUMENT_TEXT_DETECTION`, English | `VOINEY_LAB_GOOGLE_VISION_API_KEY` (sent as `X-Goog-Api-Key`, never in the URL) |

Only the pages the extraction marked `ocr_required` are rendered (PyMuPDF,
300 dpi PNG, in memory) and sent; the other pages keep their text layer and are
labelled `pdf-text-layer`. With both engines configured each such page goes to
both: CLOVA's text is used when Hangul is at least 30% of the letters it read,
otherwise Google's; if one engine fails or times out the other's text is used;
if the two read different numbers or units the page carries
`numeric_review_required` and a warning in the result data, which blocks
nothing. With one engine configured, that engine is used alone. With none,
the endpoint returns `protocol_ocr_not_configured` and preserves the immutable
PDF. Every page records the engine and version that produced it. Keys,
secrets and the invoke URL are never logged or returned. These adapters are
contract-tested against fake transports; neither has been called from this
repository with real credentials yet. The contract they implement is in
`src/voiney_lab/protocol_ocr.py`. Credentials and provider choice stay outside
HTTP input and the voice execution path.

Missing provider configuration is persisted as an actionable failure with retry;
it is not displayed forever as an unexplained `analysis_required` state.
Unsupported conditions, ambiguities, critical missing values, conflicts,
unreviewed/invalid OCR, corrupt/encrypted PDFs, and unsafe files fail closed.

Long text-native onboarding is evidence-first. Documents over eight pages, or
documents that exceed the existing single-pass request envelope, are split into
page-aligned chunks without lowering the 192 KiB per-chunk text ceiling. Each
established eight-core-page window is further subdivided at a deterministic
4 KiB core-source target to bound expected claim-output cardinality; an atomic
page is never split. The provider returns a small claim DTO rather than one
`ExperimentProtocol` per chunk and selects compact request-scoped evidence
handles. The server resolves those handles to immutable source identity and exact
contiguous excerpts. Deterministic validation rejects a whole chunk on any
fabricated, stale, non-contiguous, or out-of-scope evidence; merge requires every
planned chunk and complete page coverage before whole-document consistency
validation and final domain assembly.
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
VOINEY_LAB_SEMANTIC_INTENT_ENABLED=true
VOINEY_LAB_SEMANTIC_INTENT_MODEL=grok-4.20-0309-non-reasoning
VOINEY_LAB_SEMANTIC_INTENT_TIMEOUT_SECONDS=2.5
VOINEY_LAB_SEMANTIC_INTENT_MIN_CONFIDENCE=0.6
VOINEY_LAB_SEMANTIC_INTENT_MUTATION_MIN_CONFIDENCE=0.85
```

The dedicated non-reasoning model keeps this small typed classification off the
slower general reasoning path. The 2.5-second value is a hard provider boundary,
not permission to retry; the request uses no tools and caps its output at 160
tokens.

Every turn publishes a privacy-safe ruling on `turn.route_decision` under
`semantic_fallback` (`status`, `reason_code`, `proposed_intent`, `accepted`,
`confidence`, `latency_ms`) - reason codes and enum values only, never
utterance text or model prose.

## LLM router (off by default)

Decision D1 (2026-10-02, AGENTS rule 3): behind deterministic front rules,
intent judgment moves to one LLM router; a state change stays a tool proposal
that the server validates and carries out. **It is off unless
`VOINEY_LAB_LLM_ROUTER_ENABLED=true`** (no launcher sets it; turning
it on in development or the pilot is decided by the people running it, from
the lane R evaluation). Off, the voice path is exactly the one described
above. The router role's `VOINEY_LAB_ROUTER_PROVIDER` (default `xai`),
`VOINEY_LAB_ROUTER_MODEL` (default `grok-4.20-0309-non-reasoning`) and
`VOINEY_LAB_ROUTER_REASONING` choose the model (see [Model providers by
role](#model-providers-by-role)), and `VOINEY_LAB_LLM_ROUTER_TIMEOUT_SECONDS`
(default 2.5) how long a turn waits for it; it needs that provider's key.

The router and the semantic-intent fallback are never both on: with
`VOINEY_LAB_LLM_ROUTER_ENABLED=true` and
`VOINEY_LAB_SEMANTIC_INTENT_ENABLED=true` the server refuses to start and says
why (lane M1, decision 4) -- one turn is decided along one line.

On, a turn goes:

- **Front rules** — `CuratedProtocolSession.front_plan()` plans pause and
  "종료" words, a yes/no to an open question, replies while an endpoint
  question is open, an endpoint stated at a repeat-until step, timer status,
  "그거", repeat, cancel, transcript quality, a completion naming the current
  step, and a start of an experiment never started or already ended. These
  never wait on a model.
- **One model call** — otherwise `llm_router.route_turn_with_llm_router` sends
  the server snapshot, the nearby protocol steps with their facts, and the
  router history, and the model replies with exactly one call: `answer`,
  `change_state` (start, next, stop, pause, resume, start_timer) or
  `record_log` (observation, anomaly).
- **Server ruling** — a proposal is validated and carried out through the same
  branches `plan()` uses. `next` never moves on: it opens the completion
  question (or the endpoint question) and only the researcher's answer moves
  on. `stop` needs "종료" and asks "실험을 종료할까요?". `start_timer` runs the
  source duration and asks "원문은 15분입니다. 15분으로 시작할까요?" when
  another is said. An observation is recorded only when the words ask for
  it (메모, 기록, 관찰, 적어, ...); an anomaly that does not read as a problem is
  asked about first, "이상 사항으로 기록할까요?".
- **Answer checks** — an answer is used only if its numbers come from the cited
  facts, it claims no state change, writes no screen label, repeats server
  values exactly, and (for an explanation the PDF does not give) explains a
  term of the protocol in at most 120 characters with no numbers, method,
  safety or completion content. The server marks such an answer "PDF 밖
  설명이니 유의" on the screen and says "PDF에는 따로 설명이 없어요." first.
- **Fallback** — when the model is late, fails, says nothing usable, is
  refused, or its answer fails a check, the turn takes the rules' own path,
  exactly as with the router off ("PDF에서 확인할 수 없어요." where the rules
  have no answer to a rejected one).

Every test is offline and fake-backed (`tests/router_fakes.py`); with a fake
model that proposes `next`, `stop` or `start` on every turn the front rules
hand on, no step is moved, no session ended and no protocol restarted
(`tests/test_llm_router.py`, `AdversarialModelTests`).

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

## PDF text extraction

One PDF engine reads every Protocol PDF, behind one module.

| Component | Job | Licence |
| --- | --- | --- |
| `pymupdf` (PyMuPDF / MuPDF), only through `src/voiney_lab/pdf_text_engine.py` | Page count, encryption, document metadata, page text, text blocks (coordinates, font size, bold), and page images for OCR. Runs in a child process per document. | AGPL-3.0 — accepted on 2026-10-02 on the condition that it stays behind that one module, so it can be replaced there without touching the rest of the server |
| `pypdf` | Not used to read Protocol PDFs. Still used by `curated_protocol.py` and by the tests' PDF fixture writers. | BSD-3-Clause |

What `experiment_protocol_pdf.py` records about a source, independently of the
engine: the file type, the 64 MiB bound (checked before the engine runs), the
SHA-256 of the exact bytes, and a refusal when the file changes or is replaced
while it is being read. A PDF whose cross-reference table MuPDF has to rebuild
is refused as malformed.

Page text is MuPDF's own characters, with one layout rule: a line that overlaps
the previous line vertically by at least half its height and starts to its
right is the same printed line and is joined to it with one space, so a step
number set apart from its instruction stays on the instruction's line. Measured
on 2026-10-02 over the four local sources (33 pages), PyMuPDF and the previous
engine (pypdfium2) gave the same characters on every page; only whitespace
differed, and the numbered-step trigger found the same labels on every page.

There is no engine cross-check any more. Until 2026-10-02 pdfium's text was
compared with poppler's `pdftotext`, unmapped glyphs were read back from the
document's ToUnicode map, one page's disagreement refused the whole document
(`source_text_cross_check_failed`), and a host without `pdftotext` sent every
source to a reviewer (`source_text_cross_check_unavailable`). All of that is
removed; the two readiness codes are no longer produced. Evidence still has to
appear verbatim on its cited page — that check now runs against PyMuPDF's text,
with the same whitespace normalization as before.

A page with no text layer (a scan), one with any U+FFFD (a glyph the PDF gives
no Unicode mapping), or one where at least 5% of the visible characters are
private-use or unassigned code points, is marked `ocr_required` with
`ocr_reason` `no_text_layer` or `unreadable_glyphs`. U+FFFD characters are left
out of the page text and its blocks -- never replaced -- and the page warning
says how many. One is enough: on ANKOM page 3 two such glyphs (code 0 of a
Type3 font, its missing-glyph box, absent from its ToUnicode) stand directly
before a duration value, where the previous engine dropped them silently. The
document is not refused; the extraction lists those pages in
`ocr_required_page_numbers` and in a warning. Text blocks are recorded on each
page for the next structure measurement; nothing decides on them yet.

Changing the engine changes page text byte for byte, so it changes every page
hash and every evidence segment ID (`seg-…`) derived from it. Analyses stored
before 2026-10-02 cite pdfium-derived IDs and page hashes; whether they are
re-analysed or re-pointed is an open decision, not something this change does. The curated Candidate A timer manifest was
re-pointed to the new segment IDs (same pages, same literals, same
non-whitespace segment text).

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
uvicorn voiney_lab.server:app --host 127.0.0.1 --port 8000
```

The optional safety-handoff worker remains separate from the low-latency voice
loop:

```bash
python -m voiney_lab.worker
```

### Launchers

Two wrappers around that same `uvicorn` process exist so the two runtime
profiles are not assembled by hand. Both bind `127.0.0.1` by default and take
`HOST` / `PORT` from the environment; `server.py` calls
`load_dotenv(..., override=False)`, so what a launcher exports wins over the
same key in a repo-root `.env`.

```bash
./scripts/run_dev.sh                 # development, port 8000
./scripts/run_dev.sh --bootstrap-only  # load the curated fixture, do not serve
./scripts/run_dev.sh --test-mode       # also skip execution readiness gates
./scripts/run_pilot.sh               # controlled pilot, port 8080
./scripts/run_pilot.sh --check-only  # print the configuration, do not serve
```

`scripts/run_dev.sh` is the full development launcher: it verifies the
Candidate A fixture and its externally licensed source PDF by SHA-256, loads
the curated fixture, and enables the xAI-dependent optional features. Its
`--test-mode` flag sets `VOINEY_LAB_USAGE_SCOPE=demo` and
`VOINEY_LAB_TEST_MODE_SKIP_READINESS_GATES=true` and says so loudly;
without the flag neither variable is set. `scripts/run_candidate_a.sh` is the
former name and now forwards to it.

`scripts/run_pilot.sh` loads no fixture, keeps its state under
`data/runtime/pilot/`, and turns every feature that reaches outside the
approved source documents off — `VOINEY_LAB_EXTERNAL_REFERENCES_ENABLED`,
`VOINEY_LAB_SUPPLEMENTAL_MODEL_KNOWLEDGE_ENABLED`, `VOINEY_LAB_WEB_VISUAL_SEARCH_ENABLED`,
`VOINEY_LAB_GENERATED_VISUALS_ENABLED` — along with
`VOINEY_LAB_MOSS_ENABLED`. Each keeps a value the operator exported
themselves, so enabling one is a deliberate act taken before startup. Test
mode is forced off whatever the environment said. Dry-lab workflows and the
eLabFTW ELN write-back have no flag of their own: both sit behind the
commercial workspace that the reviewer inbox and experiment timeline also
need, and both stay inert until an admin configures and verifies a connector,
so the launcher reports them rather than disabling them.

The pilot's approved safety documents are fixed by the launcher, never by a
`.env`. It exports `VOINEY_LAB_SAFETY_CATALOG` as the absolute path
of `data/runtime/pilot/approved_safety_catalog.sqlite` and
`VOINEY_LAB_USAGE_SCOPE=reference_only`; a different value already
exported in the shell is reported with `[WARN]` and ignored. `reference_only`
is the one scope a pilot without an identity provider can run: `operational`
needs OIDC, without which every `/api` and `/ws` request fails identity
resolution, and `demo` and `test_only` need a document of that scope in the
catalog, which is demo material by definition. Under `reference_only` the
server uses development identity and tells the model the material is
non-operational and must not be described as an approved procedure.

Before it serves, and under `--check-only`, the launcher counts the catalog
read-only and refuses with exit status 1 when the file is missing, cannot be
read as a catalog, holds any demo document (scope `demo`/`test_only`, or a
title containing "fictional", in any approval state), or has no approved,
active `reference_only` document. `--check-only` exits 0 only when none of
these holds, so a checkout without a reviewed catalog reports exit 1;
`docs/APPROVED_DOCUMENT_OPERATIONS.md` §4 describes how to put one in place.

The development launcher differs on each of these points. `run_dev.sh` takes
the safety catalog and scope from the environment or `.env` (`--test-mode`
sets the scope to `demo`), keeps its state under
`data/runtime/candidate-a-live-acceptance/`, and serves on port 8000. With no
catalog file in the `demo` or `test_only` scope, its step safety card falls
back to the fictional records in
`data/fixtures/approved_safety_manual.demo.json`; under `reference_only` or
`operational` there is no fallback and the card keeps only the step's own PDF
warning. The pilot never reaches that fallback, because it does not start
without its catalog.

### Setting names

Every setting the code reads carries the `VOINEY_LAB_` prefix (decision of
2026-10-04). `src/voiney_lab/setting_names.py` lists each one with its `.env`
area, default and meaning, and a test keeps that table equal to the names the
code reads. Names other software defines keep their own (`XAI_API_KEY`,
`XAI_BASE_URL`), as do the launchers' `HOST` and `PORT`.

Nothing reads an old name. The server, the handoff worker, the launchers and
pytest refuse to start while one is set in the environment or the repository
`.env`, and say how many and which -- names only, never a value. Rename them in
a `.env` with `python scripts/migrate_env.py --check` (names only, writes
nothing) and then `--write`, which backs the file up as
`.env.bak-YYYYMMDD-HHMMSS` (mode 600) and rewrites it grouped by area with every
value unchanged. `docs/MIGRATION_NOTES.md` records the change.

### Model providers by role

Decision of 2026-10-04 (lane M1): each role names its provider, model and
reasoning, and the people running the pilot choose them from our evaluation
sets -- performance first, cost second. Every default is the provider and
model the code called before, so an environment that sets none of these
behaves as before.

| Role | Used by | Default provider · model |
|---|---|---|
| `ROUTER` | the LLM router's one call per voice turn (tools) | `xai` · `grok-4.20-0309-non-reasoning` |
| `ANSWER` | the brain's answers, the approved-document answer, the multi-brain roles, the hand-off worker | `xai` · none: the brain requires it, the worker uses `grok-4`, the multi-brain roles `grok-4.6` |
| `TRANSLATION` | a revision's Korean and the reader translation of a step | `xai` · `grok-4.6` |
| `ANALYSIS` | the structured PDF protocol analysis | `xai` · none (required), reasoning `high` |
| `REPORT` | the experiment report's prose | `xai` · `VOINEY_LAB_SUPPLEMENTAL_MODEL`, then `grok-4.6` |
| `SUPPLEMENTAL` | outside-the-PDF explanations and the web reference search | `xai` · `grok-4.6`, reasoning `low` |

Each role reads `VOINEY_LAB_<ROLE>_PROVIDER` (`xai`, `anthropic`, `openai` or
`google`), `VOINEY_LAB_<ROLE>_MODEL` and `VOINEY_LAB_<ROLE>_REASONING`
(`none`, `low`, `medium`, `high`, `xhigh`, `max`; unset leaves the provider's
default). `src/voiney_lab/model_providers.py` moves each provider's request and
reply to and from the chat-completions shape the code reads: xAI through the
OpenAI SDK as before, OpenAI through its Responses API, Anthropic through the
Messages API (with `cache_control` on the stable system blocks) and Google
through `google-genai`: the Gemini API with `GEMINI_API_KEY` by default, or
Vertex AI (now "Gemini Enterprise Agent Platform") when the SDK's own
`GOOGLE_GENAI_USE_ENTERPRISE=true` (legacy `GOOGLE_GENAI_USE_VERTEXAI`) is set
-- with `GOOGLE_API_KEY` (express mode), or with `GOOGLE_CLOUD_PROJECT` and
`GOOGLE_CLOUD_LOCATION` (default `global`) through Application Default
Credentials. The server's validation of a proposal,
its answer checks and the translation checks are the same for every provider.
The web reference search is xAI's `web_search` tool, so
`VOINEY_LAB_EXTERNAL_REFERENCES_ENABLED=true` with any other
`VOINEY_LAB_SUPPLEMENTAL_PROVIDER` is refused at start-up. The adapters are
contract-tested against fake SDK clients (`tests/test_model_providers.py`), not
live-tested by the suite.

The settings that named models before (the chat, worker, multi-brain,
protocol-analysis, report-writer, external-reference and supplemental model
settings, and their reasoning settings) are old names now:
`scripts/migrate_env.py` renames them, and the server refuses to start while
one is set. Two of them in one `.env` that became the same role setting stop
the tool; keep the one you want.

### Core configuration

| Variable | Purpose |
|---|---|
| `XAI_API_KEY` | Server-only xAI credential (the default provider of every role) |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY` | Credentials of the other providers, read only when a role names them |
| `VOINEY_LAB_<ROLE>_PROVIDER`, `_MODEL`, `_REASONING` | The model behind each role; see [Model providers by role](#model-providers-by-role) |
| `VOINEY_LAB_ANALYSIS_MODEL` | Required structured protocol analysis model; current deployment example: `grok-4.6` |
| `VOINEY_LAB_ANALYSIS_REASONING` | Protocol-analysis reasoning effort; defaults to compatibility-preserving `high` |
| `VOINEY_LAB_TTS_VOICE` | Cascade voice; defaults to `leo` |
| `VOINEY_LAB_USAGE_SCOPE` | `operational`, `demo`, `reference_only`, or `test_only` |
| `VOINEY_LAB_SAFETY_CATALOG` | Absolute approved safety-catalog path |
| `VOINEY_LAB_PROTOCOL_ENABLED` | Enables immutable PDF catalog |
| `VOINEY_LAB_PROTOCOL_DATA_DIR` | Absolute ignored protocol-store directory |
| `VOINEY_LAB_PROTOCOL_CLAIM_CHUNKS_ENABLED` | Default-off gate for controlled evidence-first claim-chunk evaluation |
| `VOINEY_LAB_WORKSPACE_ENABLED` | Enables tenant/RBAC/source workspace |
| `VOINEY_LAB_WORKSPACE_DATA_DIR` | Absolute ignored workspace directory |
| `VOINEY_LAB_ANALYTICS_RETENTION_DAYS` | Tenant default, 1–3650 days |
| `VOINEY_LAB_EXPERIMENT_REPORTS_ENABLED` | Enables append-only experiment records |
| `VOINEY_LAB_EXPERIMENT_REPORT_DB` | Absolute report SQLite path |
| `VOINEY_LAB_TEST_MODE_SKIP_READINESS_GATES` | Default `false`. Development test mode; see below |

`VOINEY_LAB_TEST_MODE_SKIP_READINESS_GATES=true` lets an analysed
Protocol be development-activated and run while readiness gates are still
outstanding. It is honoured only in the `demo`, `reference_only` and
`test_only` scopes; under `operational` it is ignored and the startup log says
so. It does not change any readiness verdict, the outstanding-gate list, the
source-evidence validation, or service approval, and a Protocol with no
analysis, or a failed one, still cannot run. While it is on, the startup log
and a page-top banner read "테스트 모드: 실행 준비 게이트를 건너뜀", an
activation made through it carries `test_mode_readiness_gates_skipped` in the
ledger, and each experiment report opened in such a session starts with a
`test_mode_readiness_gates_skipped` event. `./scripts/run_dev.sh --test-mode`
turns it on, together with the `demo` scope; `scripts/run_pilot.sh` always turns
it off, with a `[WARN]` when the environment had it on. A server started any
other way reads it from the environment or `.env`.

`VOINEY_LAB_ANALYSIS_MODEL` is read from deployment environment configuration;
there is no hidden model fallback. Protocol analysis uses `grok-4.6` in the
current deployment example. Protocol analysis explicitly defaults to high
reasoning effort. Lower effort levels remain deployment-configurable but must
pass protocol-specific completeness and evidence validation before use. The
separate low-latency semantic-intent path keeps its dedicated non-reasoning
model and timeout settings.

The default-off claim-chunk path supplies deterministic, bounded action-block
evidence and accepts only adjacent compact request-scoped handles from the
provider. An immutable server map resolves each handle to its full canonical
segment ID, revision/document/page hashes, segment order, and exact source text.
The provider cannot return `source_text`: the server reconstructs the exact
canonical excerpt from validated handles and uses that immutable span as
canonical claim/marker source text before running the unchanged protocol evidence
validator. Provider-authored excerpts and provider-echoed canonical hashes are
not part of the response schema. Per-request schemas require exactly one coverage
record for each core page and constrain claim, marker, and coverage page numbers
to that request's core-page set; deterministic validation independently enforces
the same rules. The shared monolithic configuration intentionally does not accept
reasoning effort `none`. If claim extraction later requires a different model or
effort policy, introduce separate `PROTOCOL_CLAIM_ANALYSIS_MODEL` and
claim-reasoning configuration instead of changing the monolithic defaults.

### Identity configuration

For operational workspace mode, configure all of:

```dotenv
VOINEY_LAB_OIDC_ISSUER=https://id.example.test/
VOINEY_LAB_OIDC_AUDIENCE=voice-workflow-agent
VOINEY_LAB_OIDC_JWKS_URL=https://id.example.test/.well-known/jwks.json
VOINEY_LAB_OIDC_TENANT_CLAIM=organization_id
VOINEY_LAB_OIDC_ROLES_CLAIM=roles
VOINEY_LAB_OIDC_NAME_CLAIM=name
```

For a non-operational local demo, omit OIDC values and optionally set a JSON
allowlist in `VOINEY_LAB_DEV_AUTH_PROFILES`. If omitted, one local
lab-admin profile is created. Do not enable development identities in operational
scope.

### Connector secrets

Connector records contain opaque `secret://` references, never credential values.
The application resolves them through a server-owned environment mapping:

```dotenv
VOINEY_LAB_SECRET_REFERENCES={"secret://tenant-a/protocols-io":"PROTOCOLS_IO_TOKEN","secret://tenant-a/drive":"DRIVE_ACCESS_TOKEN","secret://tenant-a/github":"GITHUB_INSTALLATION_TOKEN","secret://tenant-a/github-webhook":"GITHUB_WEBHOOK_SECRET","secret://tenant-a/elabftw":"ELABFTW_API_KEY"}
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
voiney-replay
# Equivalent project-native invocation:
python -m voiney_lab.replay_turns
# The historical script remains a thin compatibility wrapper:
python scripts/replay_turns.py
```

Evaluate a sanitized JSON manifest of recognized/reference outcomes without
loading audio:

```bash
voiney-evaluate path/to/results.json
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
VOINEY_LAB_MOSS_ENABLED=false \
VOINEY_LAB_WORKSPACE_ENABLED=false \
VOINEY_LAB_EXPERIMENT_REPORTS_ENABLED=false \
python -m pytest -q
python -m compileall -q src tests scripts
git diff --check
```

Pass those three flags in **both** baselines below. Under pytest the server
and the worker no longer read the repository `.env` (lane M1, decision 6 --
only the old-setting-name check still looks at it), but the same names
exported in the shell would still change the result; forcing the flags off per
command reproduces the documented numbers. In a clean shell (CI, a fresh
worktree) they are a no-op, so passing them is always safe and never wrong.

No test reaches a model or OCR provider, whatever keys exist: `tests/conftest.py`
removes `XAI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`,
`GOOGLE_API_KEY` and the OCR and Moss secrets from the test process before any
test module is imported, and refuses any connection to an address outside this
machine (`tests/test_no_outside_calls.py`). Provider calls stay fake-backed.

The suite needs no system PDF tools: PyMuPDF is a Python wheel, and the
`pdftotext` comparator the suite used to expect is gone (see
[PDF text extraction](#pdf-text-extraction)).

Browser acceptance coverage for the researcher/reviewer/admin workspaces (desktop
and mobile viewports) lives under `tests/e2e/` and runs separately via
[Playwright](https://playwright.dev/):

```bash
npm install
npx playwright install --with-deps chromium
npx playwright test
```

GitHub Actions (`.github/workflows/ci.yml`) runs on every push to `main`,
`dev`, `feature/**`, `fix/**` and `refactor/**`, and on every pull request into
`main` or `dev`. It has three jobs:

- **Python tests** — runs the pytest command above
  under condition B (no licensed PDF, no `.env`), `compileall`, the
  deterministic replay, and the Candidate A evaluators, which skip because the
  PDF is absent.
- **Static check (warning only)** — pylint's `possibly-used-before-assignment`
  over `src`, `scripts` and `tests`. Findings show as warning annotations on the
  run and the pull request; the job never fails the build.
- **Playwright browser acceptance** — `playwright.ci.config.ts` with
  `scripts/run_ci_server.sh`, a credential-free server launcher that runs with
  an empty protocol catalog instead of the full Candidate A demo fixture, since
  that fixture's integrity check requires an externally licensed source PDF
  that is intentionally not committed to the repository. `GET /api/protocols`
  refuses to answer without an approved safety catalog, so the job builds one
  from the repository's fictional demo manifest
  (`data/moss_demo/approved_documents.ko.json`) and passes it to the server
  with `VOINEY_LAB_USAGE_SCOPE=demo`. A failed run uploads
  `test-results/` (traces and screenshots).

Local development still uses `scripts/run_dev.sh` (the default
`playwright.config.ts`) for full-fidelity manual testing when that PDF is
available.

The same externally licensed PDF also backs 14 pytest modules' byte-exact
source-identity checks and both `scripts/evaluate_candidate_a_*.py`
evaluators. `tests/conftest.py` skips those modules (with an explicit reason)
whenever the PDF is absent, and the CI workflow does the same for the
evaluator scripts, rather than faking the file or hiding a real failure
behind it. The Playwright browser suite and `python scripts/replay_turns.py`
run identically in CI and locally; the pytest suite does **not** — see the two
baselines below.

### Two pytest baselines

The pass count depends on whether the externally licensed source PDFs are
present, so **a reported pass count is meaningless without saying which
condition produced it.** Always name the condition when quoting a number.

Both conditions use the same command, including the three flags above.

**Condition A — licensed sources present** (the maintainer's tree;
`data/runtime/candidate-a-source/in-gel-digestion.pdf` plus the ANKOM,
intracellular-metabolite and dynamic-headspace PDFs):

```
1521 passed, 1293 subtests passed in 84.91s
```

Zero skipped, exit code 0. Measured 2026-09-11 at `bfc292b`.

**Condition B — licensed sources absent** (CI, or any fresh clone or
worktree, since `/data/runtime/` is git-ignored):

```
1198 passed, 541 skipped, 1178 subtests passed in 49.49s
```

Zero failed, exit code 0. Measured 2026-10-01 at `2e59706` in a fresh clone
with a fresh virtual environment (Python 3.14, `pdftotext` installed). A fresh
clone or a detached `git worktree` is the non-destructive way to reproduce
this condition: never move, rename, or delete the real PDF to simulate its
absence.

The two totals are not directly comparable — collection is partly dynamic
(`test_stored_payloads_still_load.py` discovers stores under `data/runtime`,
finding none in condition B).

Condition B used to fail on tests that read a local PDF without checking
that it is there. They now skip with their reason printed when the source is
absent, the way their neighbours in the same files already did:

- `tests/test_extraction_cross_check.py` — four tests in
  `UnmappedCodePointDecisionTests`
- `tests/test_numbered_label_trigger.py` — five tests across
  `LocalSourceTriggerTests`, `FixtureScopeTests` and
  `FixtureScopeKnownLimitationTests`; the two-source count skips per source,
  so in-gel is still counted wherever it is present
- `tests/test_protocol_provider_diagnostics.py` —
  `test_diagnostic_is_read_only_for_server_owned_protocol_state`

Most of them read ANKOM, a PDF in the restored store under
`data/runtime/candidate-a-live-acceptance/`, so they skip wherever that
store is absent, not only where in-gel is.

The 14 modules `tests/conftest.py` does skip when the PDF is absent:

```
tests/test_candidate_a_acceptance_phase2.py
tests/test_candidate_a_final_hardening.py
tests/test_candidate_a_live_voice_generalization.py
tests/test_candidate_a_research_hardening.py
tests/test_candidate_a_websocket_integration.py
tests/test_curated_protocol_cascade.py
tests/test_experiment_reports.py
tests/test_phase3_acceptance.py
tests/test_protocol_catalog.py
tests/test_runtime_intent_routing.py
tests/test_safety_pack.py
tests/test_semantic_intent_fallback.py
tests/test_stability_and_semantic_hardening.py
tests/test_transcript_admission.py
```

Two of these carry weight out of proportion to their number:
`test_protocol_catalog.py` holds the only active test of the
"registration requires `application/pdf`" premise, and
`test_curated_protocol_cascade.py` is the largest test module in the
repository. Work that changes either premise should be verified under
condition A, because under condition B the tests that defend them are not
running.

Tests are provider-free unless explicitly marked otherwise. Connector and
eLabFTW contracts use fakes; the real adapters remain in the production code
path. The current integration classification is in the
[`Capability Matrix`](docs/CAPABILITY_MATRIX.md).

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
- uvicorn's access and WebSocket lines keep each query value only as its length
  (`?search=<5 chars>`); a line in any other shape loses its query string. The
  handoff worker logs a report's location by length and SHA-256 prefix.

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
