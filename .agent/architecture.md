# Current architecture

VoineyLab (formerly Voice Workflow Agent; the company is Voiney) -- an MVP
prototype, not field-validated. Revised 2026-10-10 (lane CL): the
approved-safety and lab-reference searches, the safety report tools and their
hand-off worker, and the Moss connection were deleted; each role's provider
is a setting (the table says "설정 필요", `.env.example` has the values in use).

## Runtime topology

```text
Browser
  AudioWorklet → 16 kHz PCM frames → /ws
  canonical events/audio segments ← LockedSender ← FastAPI
                                               │
                                  ListenerSession + VAD
                                               │
                                 STT (provider setting)
                                               │
                                    RequestArbitration
             ┌─────────────────┬───────────────┐
             │                 │               │
       emergency gate   curated runtime   general brain
                         router          (no protocol,
                                          no tools)
             │                 │               │
             └─────────────────┴── server-owned state
                                               │
                               segmented TTS (provider setting)
```

Only the Cascade path exists. The server advertises `pipelines:["cascade"]` and a
non-secret voice profile. `VOINEY_LAB_TTS_VOICE` defaults to the chosen provider's
voice (`google_cloud`: `ko-KR-Chirp3-HD-Charon`); there is no active
Realtime/Native configuration or transport.

Where the 16 kHz above comes from: one module constant, `SAMPLE_RATE` in
`audio.py`, which the rest of the pipeline follows. `SAMPLES_PER_FRAME` and
`FRAME_BYTES` are derived from it, `vad.py` passes it to `is_speech`, and
`pcm_to_wav` defaults its header to it. It is not a value the VAD forces:
`webrtcvad` accepts 8000, 16000, 32000, and 48000 (measured against the
library's own `valid_rate_and_frame_length`; 22050 and 44100 are refused).
Whether a specific provider requires it is **not** something this repository
records — no provider's STT rate specification is present here, so do not state one.
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
2. The STT provider (ElevenLabs `scribe_v2` in `.env.example`) returns
   structured transcription metadata.
3. admission gates reject empty/non-speech/keyterm echo.
4. `arbitrate_request` produces one immutable request classification.
5. `route_curated_runtime_turn` (the rules' path) executes the route. The
   general brain answers only a session without a protocol, which a voice
   session never is (cascade refuses to start without one); since lane CL it
   has no tools.
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

`VOINEY_LAB_LLM_ROUTER_ENABLED` (default `false`), the router role's
`VOINEY_LAB_ROUTER_PROVIDER`/`_MODEL` ("설정 필요"; `.env.example`: openai
`gpt-6-luna`) and `VOINEY_LAB_LLM_ROUTER_TIMEOUT_SECONDS` (default 2.5). Off, `run_turn` calls `route_curated_runtime_turn` as before and
nothing below runs. On:

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
         → the rules' own route (route_curated_runtime_turn);
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
  → text-empty branch: trusted OCR adapter, run by the upload itself
  → exact-source/page validation + append-only OCR evidence
  → analysis job started by the upload (single-pass or page-bounded evidence claims)
  → per-claim source-bound segment selection + server-owned exact-excerpt resolution
  → complete-chunk deterministic merge + whole-document consistency gate
  → typed ExperimentProtocol validation
  → fail-closed readiness assessment
  → source-linked analysis result (the start screen's summary)
  → execution verdict at read time: analysis passed + no execution blocker
  → the experimenter presses "이 프로토콜로 시작" (writes safety_notices_acknowledged)
  → executable CuratedProtocolFixture
```

The analysis result is read-only and includes prerequisites, materials,
equipment, sections, steps, sub-actions, quantities, timers, observations,
warnings, missing values, advanced constructs, readiness reasons and the
execution rule's two lists (lane DI, 2026-10-08). `assess_readiness` is
unchanged; the catalog splits its reasons when it is read: an invalid
protocol, a failed source-evidence check, no executable step, a page still
needing OCR and a safety-critical conflict block the start
(`execution_blockers`); every other reason is an `execution_notice` read on
the start screen, and a construct the guidance cannot handle yet (parallel
work, a recurring step, a reusable subprocedure) is said once at its step --
"이 단계의 동시 작업은 아직 안내 기능이 없어요. 원문을 읽어 드릴게요." --
and the source is read. There is no approval, revocation, reviewer finding or
development activation.

Structured protocol analysis requires the deployment-supplied
`VOINEY_LAB_ANALYSIS_MODEL`; the values in use are google `gemini-3.8-flash`
at reasoning `low` (lane AQ, 2026-10-10).
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
execution authority. HTTP callers cannot choose a provider; the server accepts
only a deployment-injected `ProtocolOcrProvider`. Results must match the
immutable PDF SHA-256 and contain every page exactly once and in order within
per-page/document limits. Completed and failed states are append-only protocol
events. The upload runs OCR and then the analysis by itself (lane PX); the OCR
text is accepted as source text automatically and the experimenter's start
covers it (lane DI, 2026-10-08). OCR never produces an executable revision on
its own: the analysis and the execution rule still decide.

## Evidence and provider boundaries

Authority order is the active protocol's source first. The approved safety
catalog feeds the step safety card; the voice answer from it (the research
path's step 1, `search_approved_lab_references`) was deleted on 2026-10-10
(lane CL). Optional external text research (xAI only, off by default), the
short outside-PDF explanation ("AI 일반 지식", `external_references.py`
`explain_outside_pdf`) and supplemental model knowledge are marked as
reference context and cannot mutate workflow state.

A picture request ("그림 보여줘", "사진 보여줘") is recognised by the rules
(`_WEB_VISUAL_REQUEST_PATTERNS`). The xAI web-image search and image
generation were removed on 2026-10-08 (lane DI) and lane WV (2026-10-09)
rebuilt pictures on OpenAI: the step's own source figure when it has one;
otherwise, since lane VF, with `VOINEY_LAB_DRAWN_DIAGRAMS_ENABLED` a drawing
made at once ("AI 가 그린 그림"), and for a named thing's photo with
`VOINEY_LAB_WEB_EXPLANATIONS_ENABLED` a web lookup with its sources; with both
off, the guidance sentence as before. The Snakemake/Nextflow metadata lane
(`drylab_workflows.py`) was removed on 2026-10-08.

## Persistence and reporting

- Protocol catalog: SQLite plus content-addressed source objects.
- Experiment workspace: SQLite (schema 9), the experimenter's durable
  ExperimentSession with checkpoints, observations, evidence metadata and
  recovery. The approval, adaptation, connector, inbox, dry-lab, ELN-audit,
  membership and analytics tables stay in the schema but nothing writes to
  them any more (lane DI, 2026-10-08; see `docs/MIGRATION_NOTES.md`).
- Experiment reports: append-only SQLite metadata/events associated by session
  identity with the durable tenant ExperimentSession, plus deterministic
  JSON/Markdown/CSV/DOCX exports.
- The safety report hand-off (a JSONL queue and a separate worker writing EML
  files) was deleted on 2026-10-10 (lane CL), with the model tools that filed
  and checked reports.
- Runtime metrics: bounded in-memory aggregates derived from event allowlists.

`GET /api/admin/metrics` requires a configured shared token and returns aggregate
report, catalog, route, intent, tool, and latency data. It excludes audio,
transcripts, free text, private titles, and identifiers. A shared token is an MVP
boundary, not a substitute for a production login.

## Frontend authority

The browser cockpit is one experimenter screen (lane DI, 2026-10-08). It
renders canonical server snapshots/events and never infers completion or
workflow state from assistant prose. Upload handling has explicit OCR,
analysis polling/retry and start-screen states (analysis summary, safety
statements with their Korean, execution blockers, notices); the one button is
"이 프로토콜로 시작". Turn cards keep route/tool/latency diagnostics in an
expandable region.
