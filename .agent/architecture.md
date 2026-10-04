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
non-secret voice profile. `VOINEY_LAB_TTS_VOICE` defaults to `leo`; there is no active
Realtime/Native configuration or transport.

Where the 16 kHz above comes from: one module constant, `SAMPLE_RATE` in
`audio.py`, which the rest of the pipeline follows. `SAMPLES_PER_FRAME` and
`FRAME_BYTES` are derived from it, `vad.py` passes it to `is_speech`, and
`pcm_to_wav` defaults its header to it. It is not a value the VAD forces:
`webrtcvad` accepts 8000, 16000, 32000, and 48000 (measured against the
library's own `valid_rate_and_frame_length`; 22050 and 44100 are refused).
Whether a specific provider requires it is **not** something this repository
records — no xAI STT rate specification is present here, so do not state one.
The browser resamples to it once, in `resamplePcm16`, from whatever rate its
`AudioContext` chose. Note that the input and output paths each hold their own
`16000` literals — the server's `ready` event and STT diagnostic records on one
side, the TTS `output_format` and the browser's `createBuffer` calls on the
other — so changing capture and changing playback are separate edits.

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
current-step, general QA, and unknown requests on the running path.
`completion_intent.py` and other legacy helpers are compatibility projections.
With the LLM router below enabled, it takes the turns the front rules hand
on (AGENTS rule 3); off (the default), every turn takes the rules' path.

`runtime_routing.py` is the production curated boundary. `curated_protocol.py`
returns a plan with explicit action, answer origin, checkpoint mutation, claim
requests, and limitations. Read-only plans must compare equal before/after at the
checkpoint level. A combined learning+next plan stages a pending completion frame;
only a later explicit confirmation can advance.

### LLM router (decisions D1 and 2026-10-03; wired behind a setting, off by default)

Decision D1 (2026-10-02) moves intent judgment behind the front rules to one
LLM router, while state changes stay a tool proposal plus server validation:
the model proposes, it never changes state. The decision of 2026-10-03 makes
the router the line every turn goes along -- front rules, router, server
validation -- and allows it to choose the model or tool per situation; what
is forbidden is two paths separately deciding the same turn's state change.

`VOINEY_LAB_LLM_ROUTER_ENABLED` (default `false`), `..._MODEL`
(default `grok-4.20-0309-non-reasoning`) and `..._TIMEOUT_SECONDS` (default
2.5). Off, `run_turn` calls `route_curated_runtime_turn_with_semantics` as
before and nothing below runs. On:

```text
STT → emergency gate (server.py, F1)
    → CuratedProtocolSession.front_plan()      F2–F9, D2, D9, start: planned by rule
         │ None (the turn is handed on, session untouched)
         ▼
      llm_router.route_turn_with_llm_router: one streamed call,
        tools = answer | change_state | record_log, one call required,
        context = server snapshot + protocol context (D13) + router history (D14)
         ├─ answer → answer_check_failures (answer_checks.py gates)
         │           → CuratedProtocolSession.apply_router_answer (read-only)
         └─ change_state / record_log → llm_router.validate_tool_proposals
                      → CuratedProtocolSession.apply_tool_proposal
                           → _execute_turn_intent  (the same branches plan() uses)
      timeout · provider error · nothing usable · refused · answer rejected
         → the rules' own route (route_curated_runtime_turn_with_semantics);
           a rejected answer the rules cannot answer either says
           "PDF에서 확인할 수 없어요."
```

- `front_plan()` is `plan()` stopped once the rules have read the turn. A
  front rule's turn (`FRONT_RULES`) is planned by plan()'s own branches, so the
  plan is identical; any other turn returns `None` with the one-turn questions
  it cleared put back. The front rules: F2 transcript quality, F3 pause words
  and "종료" commands, F4 a yes/no to an open question, F5 any reply while an
  endpoint question is open and an endpoint stated at a repeat-until step with
  no question open (D9), F6 time left, F7 "그거" (D3), F8 repeat, F9 cancel a
  lookup, a completion naming the current step (D2), and `start_command`: a
  start of an experiment never started, or any start after it ended, which
  is answered "이 실험은 이미 끝났어요. 다음 실험은 화면에서 프로토콜이나 세션을
  골라 시작해 주세요." and changes nothing (decision 2, 2026-10-03).
- The model is shown the server snapshot (phase, current step, open question,
  timer, revision), the current step and two either side with their facts
  (each with an id such as `S7.current_step`), every step's label and title,
  the protocol-wide facts and terms -- as data blocks, never instructions --
  and the router history. The answer is a function call (`answer`) beside the
  two tools, so a reply is always exactly one call.
- `llm_router.py` rules on a proposal: one per turn; made for this turn,
  generation, revision and step; no open question; evidence verbatim in this
  turn's words and carrying the action's own word; no question or
  hypothetical; the current step only. `next` only ever opens the completion
  question, or the endpoint question at a repeat-until step (D2); `stop` needs
  "종료" and asks once (D5); `start_timer` runs the source duration and asks
  before any other (D6); `resume` only lifts a pause; `start` only starts a
  protocol that never started. `record_log` takes an observation only with a
  word of recording in its evidence and an anomaly only when the words read
  as a problem; any other anomaly is asked about once, "이상 사항으로
  기록할까요?" (decision 1, 2026-10-03). A refused proposal takes the rules'
  route; `apply_tool_proposal` keeps its server-written refusal replies for
  direct callers.
- `apply_tool_proposal()` turns an accepted proposal into a
  `CuratedControlIntent` (`confidence_source="llm_tool_proposal"`) and runs it
  through `_execute_turn_intent`, the branches and post-turn gates `plan()`
  uses. The D6 duration question and the decision-1 anomaly question are
  one-turn questions like the end question: answered by a front yes/no, held
  through a pause (D10), in the checkpoint.
- An answer is used only when `answer_check_failures` passes: numbers (with
  or without a unit) only from the cited facts, a `pdf` answer cites given
  facts, no state-change claim, no screen label, server values exact, an
  outside-PDF explanation within D4, and the state unchanged while the model
  wrote. `apply_router_answer()` sends it as separate values (body text,
  `display_document` source and citation sections, development information
  only in test mode); an outside-PDF answer gets the server's marks: a "PDF 밖
  설명이니 유의" notice section and "PDF에는 따로 설명이 없어요." said first.
- `ConversationHistory.record_router_turn()` keeps every routed turn, front
  ones too: the step, the words, the handler (`front:<rule>`, `llm`,
  `llm+tool`, `fallback_rules`), the proposal's tool and action (never its
  evidence or value), the server's result and at most 200 characters of the
  spoken reply; six bundles and about 1,200 tokens (D14). State is read from
  each call's server snapshot, never from history.
- `turn.state` says `composing` once the answer starts to stream;
  `turn.route_decision` carries a `router` field (handler, fallback reason,
  model, verdict, timings, token usage) only when the router ran.

## Protocol lifecycle

```text
raw PDF stream
  → size/media/encryption/structure checks
  → immutable source bytes + SHA-256 + extracted pages
  → text-empty branch: trusted OCR adapter
  → exact-source/page validation + append-only OCR evidence
  → human accept/reject against the PDF
  → explicit analysis job (single-pass or page-bounded evidence claims)
  → per-claim source-bound segment selection + server-owned exact-excerpt resolution
  → complete-chunk deterministic merge + whole-document consistency gate
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

Structured protocol analysis requires the deployment-supplied
`VOINEY_LAB_PROTOCOL_ANALYSIS_MODEL`; the current deployment example is `grok-4.6`. This is
separate from the bounded low-latency semantic-intent model and timeout policy.
When the default-off deployment gate
`VOINEY_LAB_PROTOCOL_CLAIM_CHUNKS_ENABLED` is enabled, text-native
documents over eight pages enter the evidence-claim path even when their
extracted byte count is small. Its provider DTO is not ExperimentProtocol:
it contains page coverage, source structure markers, and independently evidenced
scientific/execution claims. Pages are exposed as deterministic, bounded
numbered-action blocks with compact request-scoped handles. Established
eight-core-page windows are subdivided at a deterministic 4 KiB core-source
target to bound expected provider-output cardinality without splitting an atomic
source page. An immutable
server-side map binds each handle to the canonical segment identity, revision,
document hash, page, page-text hash, segment order, and exact text. The provider
selects only adjacent handles; the server reconstructs the exact excerpt and
rejects unknown, stale, cross-request, cross-page, reversed, duplicated, or
non-contiguous selections. Every required chunk must validate before merge,
`analysis_incomplete` coverage is terminal for that run, the 120-second bound is
one total-run deadline, and provider concurrency remains serial by default.

OCR is a source-preserving extraction boundary in `protocol_ocr.py`, not an
approval or execution authority. HTTP callers cannot choose a provider; the
server accepts only a deployment-injected `ProtocolOcrProvider`. Results must
match the immutable PDF SHA-256 and contain every page exactly once and in order
within per-page/document limits. Completed, failed, and reviewed states are
append-only protocol events. Accepted OCR text becomes input only to a later
explicit structured-analysis request; it never auto-starts analysis or produces
an executable revision.

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

## Computational workflow metadata

`drylab_workflows.py` inspects UTF-8 Snakemake/Nextflow entry points fetched by
the read-only GitHub connector. Repository, resolved commit, relative path,
source hash, engine declarations, config/schema/environment paths, and declared
rules/processes form an immutable review-required revision. The in-process
registry has no execute method; the future `SeqeraLaunchBoundary` protocol is not
implemented or called.

A wet/dry link is admitted only for a real visible durable ExperimentSession,
its source-hash-bound matching protocol lineage, and an approved metadata-only
workflow revision. Link and review events are append-only; a revoked revision
cannot be reapproved. The read API returns pinned repository/commit/path evidence
and fixed `execution_supported:false` / `execution_started:false` fields.

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
completion or workflow state from assistant prose. Upload handling has explicit
OCR extraction, page review, analysis polling/retry, structured review, and
development activation states. Turn cards
keep route/tool/latency diagnostics in an expandable region. Source and external
visuals have distinct labels.
