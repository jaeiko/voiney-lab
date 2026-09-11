# Final commercialization report

Date: 2026-08-22

Branch: `refactor/voice-workflow-agent-stability`

Baseline: `030779703d7a9bbe0bcfd839829fe7b17c817fab`

## Release disposition

The audited build is ready for controlled, fictional or non-sensitive pilot work.
It is not approved for operational, regulated, hazardous, or tenant-isolated lab
use. The distinction is deliberate: the product now has an honest runtime,
reviewable evidence, fail-closed workflow gates, and measurable pilot signals, but
it does not yet have the identity, tenancy, compliance, deployment, and field
validation controls required for a commercial production launch.

## Outcome

The highest-risk architectural defect is fixed. Production Cascade turns now pass
through the same immutable request arbitration and curated planning boundary used
by the acceptance replay. Learning, protocol audit, prior-session, uncertainty,
combined learning-plus-next, visual, and workflow-control requests can no longer
appear fixed only in a helper while the real WebSocket route behaves differently.

The PDF lifecycle is also connected end to end. A browser upload now reads the
actual nested API response, starts analysis when eligible, polls bounded progress,
shows a source-linked review, exposes retry or development activation only when
policy permits, and refreshes the executable catalog. Source bytes remain
immutable and a protocol remains non-executable when required values or supported
workflow semantics are missing.

## Implemented product changes

### Runtime and voice

- Added a typed, deterministic, non-mutating request arbiter shared by generic and
  curated conversation paths.
- Added one production curated routing boundary and a canonical
  `turn.route_decision` event containing route, intent, action, answer origin,
  fallback, and mutation outcome without transcript content.
- Preserved strict mutation authority in the curated controller; informational
  classification never authorizes a transition.
- Added bounded, source-backed responses for step rationale/warnings, protocol
  identity, missing resumable history, outcome uncertainty, and combined
  rationale/preview/confirmation.
- Kept rich legacy classification for protocol-wide, agent-meta, and
  action-specific questions so shared routing does not erase entity or claim
  detail.
- Standardized the current product on Cascade STT → agent → TTS with one canonical
  `TTS_VOICE` setting and `leo` default. Removed active documentation/configuration
  claims for a nonexistent Native path.

### Arbitrary PDF onboarding

- Added a read-only review projection containing exact document identity, pages,
  metadata, prerequisites, materials, equipment, sections, steps, sub-actions,
  quantities, conditions, timers, observations, warnings, missing values,
  constructs, readiness reasons, and analysis hashes.
- Preserved exact scientific source strings and page evidence while safely
  serializing typed values for the browser.
- Added an operational-scope block for development activation; only explicit
  demo, reference-only, and test scopes can activate a development draft.
- Added fictional commercial fixtures for a multi-step timer/quantity protocol, a
  retained conditional, and an ambiguity/missing-value failure.
- Retained existing corrupt, malformed, encrypted, oversized, OCR, long/chunked,
  retry, deduplication, and concurrency coverage.

### Web research and visuals

- Updated xAI Responses request fields to the documented web-search and citation
  contract, including `enable_image_search:true` only for the paid image-search
  fallback and `include:["no_inline_citations"]`.
- Tries public PubChem/Wikimedia sources first, then permits at most one paid xAI
  image-search candidate.
- Requires a source URL and display-rights label before image admission, validates
  remote destinations and bytes, and serves admitted images only through a
  same-origin hashed proxy.
- Removed arbitrary remote browser hotlinking. Source-only results remain useful
  as links when display rights cannot be established.
- Pure image requests do not fan out into redundant text research. A combined
  entity explanation-plus-image request may still run one bounded explanatory
  research path after the visual job is queued.

### Operations, privacy, and UI

- Added a fail-closed admin metrics endpoint protected by a server-configured
  token compared through constant-time SHA-256 digests.
- Added bounded in-memory route, intent, origin, tool, image-search, failure,
  rejection, interruption, and latency aggregates.
- Metrics explicitly exclude raw audio, transcripts, free text, prompts, private
  report titles, report/session identifiers, and model reasoning.
- Added an expandable admin view whose token is sent for one request and then
  cleared; it is never placed in browser storage.
- Added source-linked PDF review and compact expandable per-turn diagnostics.
- Browser QA found and fixed two defects after unit tests were already green: the
  development activation control remained disabled after successful analysis,
  and long review hashes forced mobile form controls beyond the viewport.

## Verification evidence

### Automated suite

- Baseline before edits: 665 tests passed, 674 subtests passed in 117.96 seconds.
- Final complete suite: 680 tests passed, 679 subtests passed in 117.67 seconds.
- Final focused commercialization gate: 20 tests passed, 5 subtests passed.
- Final compatibility repair gate: 9 tests passed, 5 subtests passed.
- Python compilation passed for all changed runtime modules.
- The production HTML script parsed successfully with Node.
- `git diff --check` passed.

The host's Python 3.12 default-executor shutdown stopped terminating idle worker
threads after the browser dependency installation. A minimal `asyncio.to_thread`
reproduction confirmed this outside the application. Two tests that already mock
their blocking work were made scheduler-deterministic: the WebSocket greeting test
executes its mocked TTS inline, and the HTTP export test still traverses the ASGI
request/response boundary while executing the synchronous endpoint inline. No
production offload behavior was removed.

### Production replay

`scripts/replay_turns.py` sent the required A–G families through the production
curated route. All seven reported `runtime_router=curated_protocol`; turns A–F
were non-mutating, and the combined learning-plus-next request staged an explicit
completion confirmation without advancing the step. The final workflow-control
family remains owned by deterministic completion and precondition gates.

### Browser pass

Chrome 151 loaded the repository's real production HTML. Because the browser
runner is isolated from the workspace loopback namespace, only the API/WebSocket
boundary was intercepted with fictional deterministic responses; backend routes
were verified separately through ASGI/WebSocket integration tests.

Verified in Chrome:

- 1440 × 1000 desktop and 390 × 844 mobile viewports;
- upload → automatic analysis → source review → explicit development activation
  → executable catalog selection;
- source hash, page identity, quantity, timer, and warning rendering;
- activation control is visible and enabled only at the allowed review state;
- admin aggregate response renders, the header is sent, and the input clears;
- no horizontal overflow or off-viewport elements at 390 px;
- zero browser console errors and zero warnings;
- expected API sequence with no duplicate paid image request.

## Current provider contract

The implementation was checked against official xAI documentation current on the
audit date:

- [Web Search](https://docs.x.ai/developers/tools/web-search)
- [Tool usage details](https://docs.x.ai/developers/tools/tool-usage-details)
- [Citations](https://docs.x.ai/developers/tools/citations)
- [Speech-to-speech](https://docs.x.ai/developers/model-capabilities/audio/speech-to-speech)
- [Text-to-speech](https://docs.x.ai/developers/model-capabilities/audio/text-to-speech)

The present application uses the documented TTS endpoint and Cascade pipeline; it
does not claim to use `grok-voice-latest` speech-to-speech.

## Recommended market wedge

The recommended first segment is a 5–50-person biotech, CRO, or core-facility team
running repeated low-hazard procedures from PDF or paper and recording results
later in an existing ELN. The product should be sold as an integration-light,
source-auditable PDF-to-bench voice layer, not an autonomous scientist or ELN/LIMS
replacement.

A 4–6 week controlled pilot should cover one protocol, 5–20 users, one export or
write-back connector, deployment/training, and a before/after outcomes report. A
$1,000–$5,000 pilot is only a hypothesis, informed partly by LabVoice's public
$1,000 one-user/one-month prototype listing; it requires 15–20 buyer interviews
before publication.

Prioritize:

1. reviewer identity, approval history, revision diff, and revocation;
2. reliable accent/noise/interruption measurements in target labs;
3. one real ELN write-back connector;
4. OIDC/SSO, tenant RBAC, audit logs, retention, and deletion controls;
5. pilot ROI based on completion, correction, rejection, latency, and write-back
   quality rather than demo engagement.

## Remaining launch blockers

- No tenant identity/isolation, OIDC/SSO, SCIM, customer RBAC, or ownership checks
  on report exports.
- No formal regulated validation package, electronic signatures, approval
  revocation, threat model, penetration test, SBOM process, or customer DPA.
- No validated OCR quality workflow or executable semantics for arbitrary DAGs,
  complex conditionals, loops, and multi-day handoffs.
- No field corpus proving microphone, accent, noise, correction, and barge-in
  performance in representative labs.
- No live-provider release gate was run with real credentials during this audit;
  latency, quota, availability, and provider data-handling remain pilot checks.
- Runtime metrics are process-local and reset on restart.

These blockers do not invalidate the controlled-pilot disposition. They do block
claims of production, regulated, or enterprise readiness.

## Canonical handoff documents

- `README.md` — current setup, architecture, workflows, and product limitations.
- `docs/CODEX_COMMERCIALIZATION_AUDIT.md` — detailed finding/remediation and
  market evidence ledger.
- `.agent/architecture.md` — actual runtime boundaries and evidence flow.
- `.agent/evaluation_strategy.md` — current release gates.
- `.agent/roadmap.md` — prioritized commercial next steps and non-goals.
- `.agent/security_rules.md` — current security and privacy constraints.

Older phase reports are retained for traceability and explicitly marked as
historical snapshots superseded by this report and the current README.
