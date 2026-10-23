# Current architecture

VoineyLab (the company is Voiney): this is the one deep description of how the code is built. It was rewritten
on 2026-10-20 (lane DOC4) against dev `e7c31c8`; every name below is in that
code. The per-module one-liners are in `AGENTS.md`, the feature-by-feature
behaviour in `docs/FEATURES.md`, and the reasons and dates in
`docs/DECISIONS.md`. Paths without a folder are under `src/voiney_lab/`.

## 1. Shape

One FastAPI/Uvicorn process (`server.py`) serves the web screen
(`static/`), the HTTP API and one WebSocket per voice session (`/ws`,
`voice_socket`). State lives in SQLite files and content-addressed source
files under the configured data folders (`data/runtime/…` in the launchers).
Protocol analysis and report writing run as background jobs inside the same
process, so there is no horizontal scaling or failover: one process, one
data folder.

```text
Browser / store-app WebView (static/index.html, connection.js, feedback.js)
  AudioWorklet → 16 kHz PCM16 frames ─────────────► /ws  (server.voice_socket)
  canonical JSON events + audio segments ◄─ LockedSender
                                                    │
                                     ListenerSession (one per connection)
                                       FrameBuffer → vad.EndpointDetector
                                       level gate (calibrated speaker)
                                                    │ utterance
                                       STT (voice_providers, provider setting)
                                                    │ transcript
                          empty / self-echo → emergency gate (emergency.py)
                          → fragment / filler / calibration / language admission
                                                    │
                     CuratedProtocolSession.front_plan  (front rules)
                          │ handled                    │ handed on
                          │                 llm_router (when enabled)
                          │                   answer │ tool proposal
                          │                          ▼
                          │              server validation (llm_router,
                          │              answer_checks) → apply_tool_proposal
                          ▼                          ▼
                     CuratedProtocolSession — the one state machine
                                                    │ plan, events
                       experiment ledger · workspace session · pilot metrics
                                                    │ reply text
                                       sentence TTS (voice_providers)

HTTP API: protocol catalog (upload, link, OCR, analysis, review cards,
start check), workspace, reports, accounts, admin, health.
```

Only the Cascade path exists (`ready` advertises `pipelines:["cascade"]`).
There is no Realtime/Native transport, and no "general brain" for a session
without a protocol: a voice session refuses to start without one.

## 2. Who owns what

| Concern | Owner | Notes |
|---|---|---|
| Workflow state (step, completion, timers, pause, observations) | `curated_protocol.CuratedProtocolSession` | The only state machine. Every change — read by a front rule or proposed by the router — runs through its branches (`plan`, `apply_tool_proposal` → `_execute_turn_intent`). |
| Turn classification label | `intent_arbitration.arbitrate_request` | A read-only label for planning and telemetry; it decides nothing on its own. |
| Rules-only routing boundary | `runtime_routing.route_curated_runtime_turn` | The path when the router is off, the router's fallback, and `scripts/replay_turns.py`. |
| Model proposals and answers | `llm_router` + `answer_checks` | Validate, then hand to the state machine. No state of their own. |
| Protocol lifecycle and the execution rule | `protocol_catalog.ProtocolCatalog` + `experiment_protocol*` | Immutable sources, append-only protocol ledger, analysis revisions, review versions, `available_for_execution`. |
| Durable experiment (the experimenter's session) | `workspace_store` | Tenant-scoped `ExperimentSession` with optimistic versions, append-only timeline, recovery after reload. |
| Experiment record and exports | `experiment_reports.ExperimentReportStore` | One append-only record per session; exports are re-derived from it. |
| Identity | `identity.IdentityResolver`, `accounts.AccountStore` | Development identity, invite-only accounts, or OIDC in `operational` scope. |
| Browser | `static/index.html` | Renders server events and snapshots; never derives workflow state from prose. |

The browser never supplies authoritative workflow state. Background results
(research, pictures, translations) check configuration, turn, generation,
protocol, revision and job identity before they are emitted; a superseded
generation is cancelled and its audio cleared (barge-in).

## 3. One turn

1. `ListenerSession.accept_chunk` feeds 20 ms frames to
   `vad.EndpointDetector`. An utterance ends after the endpoint silence
   (`VAD_END_SILENCE_MS` = 1000 ms; setting
   `VOINEY_LAB_CASCADE_VAD_ENDPOINT_SILENCE_MS`), at 15 s, or — in a
   calibrated session in a room that keeps the VAD on — when the level stays
   under the wearer's line (`EndpointResult.endpoint` = `silence`/`level`/
   `forced`). After calibration, speech far quieter than the wearer's
   reference is dropped before STT (`ignores_level`, event `speech.ignored`).
2. The waiting cue (`cascade_filler.CascadeFiller`) starts; STT returns one
   transcript per utterance.
3. Admission, in this order (`server.run_turn`): empty and self-echo
   (`ECHO_*` constants), then the **emergency gate**
   (`emergency.recognize_emergency`, fixed reply, route
   `deterministic_emergency`). Recognized emergencies do not become calibration
   samples or wait for language confirmation. Router-first recognizes the new
   utterance before joining any held fragment; only a non-emergency is joined
   and checked again, through this same fixed-response gate. Other turns continue through
   fragment join (router-first only), non-speech/filler admission, a pending
   calibration sentence and language confirmation. With a protocol the ordinary
   turn language is Korean. Audio rejected before STT still cannot reach this
   text gate; no change to acoustic recognition is implied.
4. Routing, one line (AGENTS rule 3):
   - `CuratedProtocolSession.front_plan` reads the turn first and never
     waits on a model (`FRONT_RULES`: transcript quality, pause and "종료",
     yes/no to an open question, replies to an endpoint question, time left,
     "그거", repeat, cancel, completion naming the current step, start, and
     the rest of the table).
   - Router off (`VOINEY_LAB_LLM_ROUTER_ENABLED`, default false): the rules'
     own route (`route_curated_runtime_turn` → `plan`).
   - Router on: `llm_router.route_turn_with_llm_router` makes one streamed
     call with the server snapshot, the current step ± 2 with fact ids, all
     step titles, protocol-wide facts and terms (as data blocks) and the
     router history (`brain.ConversationHistory`, 6 turns, ~1,200 tokens).
     The reply is exactly one call: `answer`, `change_state`
     (`start`, `next`, `stop`, `pause`, `resume`, `start_timer`) or
     `record_log` (`observation`, `measurement`, `deviation`, `memo`,
     `anomaly`). `validate_tool_proposals` refuses anything stale, without
     verbatim evidence in this turn, a question or hypothetical, aimed at
     another step, or while a server question is open; `next` only opens the
     completion or endpoint question; `stop` needs "종료" and asks once;
     `start_timer` runs the source duration only. An accepted proposal runs
     through `apply_tool_proposal`; an answer must pass
     `answer_check_failures` and is sent by `apply_router_answer`.
   - Fallback: a late, failed, refused or rejected router turn takes the
     rules' route for **the same turn** — never a second decision. A router
     HTTP 429 (`ROUTER_MODEL_UNAVAILABLE_KINDS`) stops the turn instead and
     says "지금 답하지 못했어요. 다시 말씀해 주세요." (`server.py`
     `ROUTER_UNAVAILABLE_WORDS`).
   - Router-first (`VOINEY_LAB_ROUTER_FIRST`, default false, needs the
     router): the front rules keep system information, ambiguous-reference
     re-asks, short clear commands and replies to server questions
     (`ROUTER_FIRST_HANDED_ON` hands the rest on), and the router may also
     call the read-only `show_visual`, `clarify` and `not_for_me`.
5. Read-only roles (`multi_brain`), outside-PDF explanation, pictures and web
   lookups run after the answer and never change state.
6. Sentence TTS streams through generation-aware audio segments;
   `turn.route_decision` carries a compact projection (with a `router`
   block when the router ran); `turn.done` and `playback.completed` close it.

A read-only request leaves every checkpoint equal before and after. A
combined "explain + next" request stages a completion confirmation; only a
later explicit confirmation advances.

## 4. Protocol lifecycle

```text
upload (PDF · .docx → LibreOffice · photo → PDF)   or   Google link (protocol_link_import)
  → size / media / structure checks, immutable bytes + SHA-256 (objects/sha256/…)
  → page text by PyMuPDF in a bounded child process (pdf_text_engine)
  → pages without a usable text layer: OCR started by the upload itself,
    two engines compared place by place, accepted automatically
  → one structured analysis (or pieces of ≤15 pages, ≤400 pages), background job
  → server-owned evidence check, requote of refused items, domain validation
  → all confirmed: analysis revision
    otherwise:     "review pending" ledger event + cards (never an analysis revision)
                   → decisions → registered review version vN (latest runs)
  → execution rule at read time: analysis (or review version) present and no
    execution blocker → available_for_execution
  → start wizard: start check (two key values) → safety statements → start
  → CuratedProtocolFixture for the session
```

- The catalog is append-only: uploads, OCR results, analysis requests and
  results, review decisions, review versions, start-check answers and
  "확인함" are events in `protocol_workspace.sqlite`. A reanalysis adds a
  revision; a run continues on the revision it started with
  (`load_fixture_for_revision`).
- HTTP callers cannot choose an OCR provider; the server injects one
  (`_install_protocol_ocr_provider`). OCR results must match the source's
  SHA-256 and cover every page once, in order.
- Execution rule (`experiment_protocol.EXECUTION_BLOCKING_REASON_CODES`):
  an invalid protocol, no executable step, a page still needing OCR and a
  safety-critical conflict block the start; every other readiness reason is
  a notice on the start screen, and a construct the guidance cannot handle
  (parallel work, a recurring reminder or action, a reusable subprocedure)
  is said once at its step before the source is read. There is no approval,
  revocation, reviewer gate or development activation.
- The start check (`start_check.StartCheck`) is enforced by the wizard's
  order only; the session start checks `available_for_execution`.
- Review cards never rewrite a safety statement (`_FIXABLE_KINDS` has no
  `warning`).

### Page evidence segments and known analysis limits

The claim-chunk path was deleted on 2026-10-13. What still holds:

- `protocol_claim_analysis.generate_page_evidence_segments` cuts a page at
  numbered-label starts, every line ending in `.`/`!`/`?`
  (`_SENTENCE_LINE_END`) and the geometric footer band, with a 4096-character
  cap. Segment ids hash the version, source, page and text: a boundary change
  must bump `EVIDENCE_SEGMENT_VERSION` (now 6) and re-check the in-gel timer
  manifest's handles. Boundaries are deterministic; approximate alignment was
  rejected because it gives unstable ids.
- Step labels are line-anchored, 1–3 digits with optional `.N` levels; a label
  followed by a number or a unit is not a step. Known false positive: a count
  before a noun ("24 crucibles").
- An unmapped glyph is never guessed: the page becomes `ocr_required`, and
  `SOURCE_PAGE_REQUIRES_OCR` blocks until OCR text is accepted and analysed.
- Repeated text is never dropped as boilerplate (hazard blocks repeat); only
  geometric furniture is set apart.
- A server-set count is never a completion condition: a repeat-until ends on
  the experimenter's observation (`endpoint_observation_outstanding`); the
  last step is decided by position.
- Known misses: values packed on one line share one segment; pages without
  end-of-line punctuation fall back to labels only; the model misses
  unnumbered, value-free instructions (conditional notes, storage). There is
  no deterministic lever for the last, and no word list should be added.
- Since 2026-10-13 nothing on the live path reports a repeat instruction or a
  value the model left out: `assess_readiness` keeps those reason codes, but
  no caller feeds them. `hollow_analysis_check` (≥ 20 source steps, < 25 %
  kept) and the prompt are the only completeness checks. Some decode-only
  names (`page_coverage`, `TextVerification`, `SOURCE_TEXT_CROSS_CHECK_*`)
  remain so that old payloads still load.
- The in-gel development fixture's timers come from
  `data/fixtures/development_protocols/candidate_a_curated_analysis.timers.json`;
  no manifest, no timers.
- Offline only: `scripts/score_extraction.py` compares saved current-format
  analysis responses with a hand-built baseline against the same PDF. It uses
  the existing parser/evidence checks and `protocol_extraction_accuracy` to
  report step and typed source-record differences. Model ids do not establish
  matches; blank/repeated labels use their page and exact quote. No model,
  protocol store or workflow state is opened and no execution verdict is made.
  This evaluator does not close the live completeness gap described above.

## 5. Evidence and provider boundaries

- Authority order: the running protocol's source first; values and safety
  statements come only from the source (passing the evidence check) or a
  value a person confirmed.
- Facility safety documents (the SOP corpus) are optional: an approved SQLite
  catalog (`document_store`, schema 2) read by `safety_pack.resolve_safety_pack`
  for the step safety card, filtered by usage scope (`operational`, `demo`,
  `reference_only`, `test_only`). `source_protocol_only` reads no catalog and
  says "기관 안전 문서 없음 · 원문 안전 주의만". `server_config` refuses a scope
  that needs a catalog it cannot use. `retrieval.py` serves only
  `scripts/audit_approved_catalog.py`.
- Outside the source: the outside-PDF explanation ("AI 일반 지식",
  `external_references.explain_outside_pdf`), the drawn diagram
  (`drawn_diagrams`), the web explanation and Commons photo
  (`web_explanations`). Each is labelled, checked by the server (numbers,
  safety, state claims removed) and read-only; none can supply a value or a
  safety statement. Web images are proxied same-origin, never hotlinked.
- Model roles (`model_providers.ROLES`): `router`, `answer`, `translation`,
  `analysis`, `report`, `supplemental`, each with a provider (`xai`,
  `anthropic`, `openai`, `google`), model and reasoning level and no default.
  Speech providers: `xai`, `google_cloud`, `elevenlabs`. OCR: `clova`,
  `google`. Background roles (`analysis`, `translation`, `report`) retry
  429/5xx and carry the Gemini safety setting; voice-turn roles do neither.

## 6. Persistence

| Store | File (launcher layout) | Schema | Written by | Append-only |
|---|---|---|---|---|
| Protocol catalog | `<protocol dir>/protocol_workspace.sqlite` + `objects/sha256/…` | 1 | `experiment_protocol_store`, `protocol_catalog` | events: yes; sources immutable |
| Workspace | `<workspace>/commercial_workspace.sqlite` + `evidence/…` | 11 | `workspace_store` | session events, completed steps, observations, evidence — SQL triggers refuse UPDATE/DELETE |
| Experiment record | `<protocol dir>/experiment_reports.sqlite` | 1 | `experiment_reports` | events by stable key (`INSERT OR IGNORE`); no triggers — enforced by code; prose is replaceable |
| Accounts | `<workspace>/accounts.sqlite` | 1 | `accounts` | no (sessions end; only hashes stored) |
| Pilot metrics | `<workspace>/pilot_metrics.sqlite` | — | `pilot_metrics` | rows of numbers, codes and times |
| Safety catalog | path in `VOINEY_LAB_SAFETY_CATALOG` | 2 | `scripts/ingest_safety_documents.py` | read-only at runtime |

- Stores are separate files; cross-store operations use exact identities and
  fail closed; there is no distributed transaction.
- Observations are `observation_only` and evidence files `not_interpreted`:
  neither becomes protocol knowledge.
- The workspace keeps tables that nothing writes any more (approvals,
  adaptations, connectors, dry-lab, ELN write-back); see
  `docs/MIGRATION_NOTES.md`.
- In-memory only: runtime metrics, web photos, drawn SVGs, report-prose job
  state, the speaker's level reference.

## 7. Identity and accounts

`VOINEY_LAB_AUTH_MODE=dev` (default) gives every request the one development
identity. `accounts` puts the `commercial_identity_boundary` middleware in
front of every `/api` path except `_ACCOUNT_OPEN_PATHS` and in front of `/ws`;
it needs the workspace. In `operational` usage scope, `identity.py` demands
OIDC bearer tokens. `_account_is_admin` is the one administrator judgment
(pilot metrics, "확인함", disabling accounts). App origins
(`VOINEY_LAB_APP_ORIGINS`) may log in with a bearer token.

## 8. Connection loss and timers

`server._send_heartbeats` beats once a second; `static/connection.js`
declares loss after 2.8 s of silence, makes the page read-only, pauses the
microphone, keeps a copy of the step timer on the server's clock and
reconnects (1, 2, 4, 8, then every 15 s) with `session.start` carrying the
experiment id and a `reconnect` block. The server restores the same
experiment (`_restore_lost_connection`, `_restore_standing_timer`), delivers
missed timer notices and records a content-free `connection_restored` event.
Timer notices are driven by a per-connection task (`_watch_timer_notices`);
the store app copies the server's timer times into device alarms and decides
nothing.

## 9. Frontend

`static/index.html` is one page with five hash-routed tabs — 홈, 프로토콜,
실험, 기록, 설정 (`TAB_PANELS`) — plus sheets: the start wizard
(`#wizard-sheet`), and during a run the conversation, figures and settings
sheets. The tab bar is hidden during a run and before login. It renders
canonical server snapshots and events only; workflow state is never inferred
from assistant prose, and text is inserted with `textContent`.
`connection.js` (loss and reconnection), `feedback.js` (end-of-run rating)
and `mic-capture-worklet.js` (capture) load with it; `native-bridge.js` does
not yet. `admin-metrics.html` is a separate page.
The upload screen's public Drive/Docs form calls `/api/protocols/import-link`
and shares the file upload's response handling, analysis/OCR polling and
result display. Link errors display server text through `textContent`.
Import is disabled during registration or a running session; stale-generation
responses are discarded. Sheets reagent-table attachment remains API-only.

## 10. Store-app shell

`mobile/` wraps the same screen in Capacitor (Android first) and adds device
features only: exact alarms, a foreground microphone service, a
Keystore-sealed login token, keep-awake, notifications. The server stays the
workflow and timer authority. See `mobile/README.md` and `docs/APP_PLAN.md`.

## 11. Failure handling

Everything fails closed and visibly, and none of it mutates workflow state:

| Condition | Result |
|---|---|
| A model role, STT or TTS provider not set | `RoleNotConfiguredError` / `ConfigurationError` naming the missing setting; no fallback provider |
| Router enabled without its provider/model | Router off, reason in `ready.research_capabilities.llm_router`; turns take the rules |
| `VOINEY_LAB_ROUTER_FIRST` without the router | `/ws` refuses the configuration |
| Outside-PDF, web or drawing switched on without its key or role | `/ws` refuses at connect |
| Router timeout, provider error, refusal, rejected answer | The rules' route for the same turn; if they cannot answer either, "PDF에서 확인할 수 없어요." |
| Router HTTP 429 | The turn stops and says so; nothing changes |
| Analysis failure | Recorded with a failure code; the previous revision stays usable |
| Old setting name present | Server, launchers and tests refuse to start, naming it |
| Safety scope needs a catalog that is missing or unusable | `server_config` refuses protocol requests (`/readyz` does not check this) |

## 12. Audio constants

The capture rate is one constant, `audio.SAMPLE_RATE` (16 kHz);
`SAMPLES_PER_FRAME` and `FRAME_BYTES` derive from it and `vad.py` passes it to
the VAD. `webrtcvad` also accepts 8, 32 and 48 kHz. The browser resamples
once (`resamplePcm16`). The output path holds its own `16000` literals (the
`ready` event, TTS output formats, the browser's buffers), so changing
capture and playback are separate edits. No provider's STT rate requirement
is recorded in this repository.
