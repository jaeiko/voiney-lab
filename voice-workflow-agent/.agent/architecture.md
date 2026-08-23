# Current architecture

## Runtime topology

```text
Browser
  AudioWorklet → 16 kHz PCM frames → /ws
  canonical events/audio segments ← LockedSender ← FastAPI
                                               │
                                  ListenerSession + VAD
                                               │
                                           xAI STT
                                               │
                                    RequestArbitration
             ┌─────────────────┬───────────────┼───────────────┐
             │                 │               │               │
       emergency gate   curated runtime  deterministic    general brain
                         router            procedure        + tools
             │                 │               │               │
             └─────────────────┴──── server-owned state ───────┘
                                               │
                                      segmented xAI TTS
```

Only the Cascade path exists. The server advertises `pipelines:["cascade"]` and a
non-secret voice profile. `TTS_VOICE` defaults to `leo`; there is no active
Realtime/Native configuration or transport.

## Turn lifecycle and ownership

`ListenerSession` owns configuration ID, protocol/revision attachment, current
turn, generation, VAD state, playback state, interruption candidates, timers,
history, visual/research tasks, and optional experiment report. Browser messages
never supply authoritative workflow state.

Accepted turns follow:

1. FrameBuffer and WebRTC VAD admit an endpoint.
2. xAI STT returns structured transcription metadata.
3. admission gates reject empty/non-speech/keyterm echo.
4. `arbitrate_request` produces one immutable request classification.
5. `route_curated_runtime_turn`, deterministic procedure gates, or the general
   brain execute the route.
6. `turn.route_decision` exposes a compact observable projection.
7. sentence TTS streams through generation-aware audio segments.
8. `turn.done` and `playback.completed` close timing ownership.

Confirmed barge-in cancels the superseded generation and clears its audio.
Background research and visual results verify configuration, turn, generation,
protocol, revision/source hash, and job identity before emission.

## Intent and state architecture

`intent_arbitration.py` is the only classifier authority for workflow control,
learning, audit, history/resume, uncertainty, combined learning+next, visual,
current-step, general QA, and unknown requests. `completion_intent.py` and other
legacy helpers are compatibility projections.

`runtime_routing.py` is the production curated boundary. `curated_protocol.py`
returns a plan with explicit action, answer origin, checkpoint mutation, claim
requests, and limitations. Read-only plans must compare equal before/after at the
checkpoint level. A combined learning+next plan stages a pending completion frame;
only a later explicit confirmation can advance.

## Protocol lifecycle

```text
raw PDF stream
  → size/media/encryption/structure checks
  → immutable source bytes + SHA-256 + extracted pages
  → explicit analysis job (single or chunked)
  → typed ExperimentProtocol validation
  → fail-closed readiness assessment
  → source-linked review projection
  → service approval OR non-operational development activation
  → executable CuratedProtocolFixture
```

The review projection is read-only and includes prerequisites, materials,
equipment, sections, steps, sub-actions, quantities, timers, observations,
warnings, missing values, advanced constructs, and readiness reasons. Unsupported
conditional/parallel/repeat constructs and missing or conflicting execution values
remain explicit and block execution.

## Evidence and provider boundaries

Authority order is active protocol/approved safety catalog first. Optional external
text research and supplemental model knowledge are marked as reference context and
cannot mutate workflow state.

Explicit image intent uses:

1. PubChem for known chemical structures;
2. Wikimedia Commons with source-license metadata;
3. at most one xAI Responses web-search request with
   `enable_image_search:true`.

External display bytes require a rights label, HTTPS/SSRF admission, MIME/dimension
validation, size bounds, and same-origin SHA-256 asset serving. Without those, only
the source link is emitted.

## Persistence and reporting

- Protocol catalog: SQLite plus content-addressed source objects.
- Procedure state: SQLite, with deterministic observation/timer/completion gates.
- Experiment reports: append-only SQLite metadata/events associated by session
  identity with the durable tenant ExperimentSession, plus deterministic
  JSON/Markdown/CSV/DOCX exports.
- ELN write-back: explicit-confirmation eLabFTW adapter that requires a completed
  durable session and matching completed report/revision, then records the
  idempotent request and append-only external identity provenance.
- Safety handoff: bounded JSONL queue and separate worker producing reviewable EML
  and status artifacts; it does not send mail automatically.
- Runtime metrics: bounded in-memory aggregates derived from event allowlists.

`GET /api/admin/metrics` requires a configured shared token and returns aggregate
report, catalog, route, intent, tool, and latency data. It excludes audio,
transcripts, free text, private titles, and identifiers. A shared token is an MVP
boundary, not a substitute for production SSO/RBAC.

## Frontend authority

The browser cockpit renders canonical server snapshots/events. It never infers
completion or workflow state from assistant prose. Upload analysis has explicit
loading, polling, retry, review, and development activation states. Turn cards
keep route/tool/latency diagnostics in an expandable region. Source and external
visuals have distinct labels.
