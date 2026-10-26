# Current architecture

Voiney Lab (the company is Voiney): this is the one deep description of how
the code is built. Every name below refers to the executable code. The
per-module one-liners are in `AGENTS.md`, the feature-by-feature
behaviour in `docs/FEATURES.md`, and the reasons and dates in
`docs/DECISIONS.md`. Paths without a folder are under `src/voiney_lab/`.

## 1. Shape

One FastAPI/Uvicorn process (`server.py`) serves the web screen
(`static/`), the HTTP API and one WebSocket per voice session (`/ws`,
`voice_socket`). State lives in SQLite files and content-addressed source
files under the configured data folders (`data/runtime/…` in the launchers).
Protocol analysis and report writing run as background jobs inside the same
process. Blocking source conversion/extraction, catalog registration and reads,
OCR status, session preparation, vocabulary and translation checks run in
worker threads. Each SQLite connection is opened, used and closed in the
same worker; cancellation waits for work still using an upload before
cleaning its temporary files. These workers share the process and stores,
so there is no horizontal scaling or failover: one process, one data folder.

```text
Browser / store-app WebView (static/index.html or web/ at /app/; voice-core.js, connection.js)
  AudioWorklet → 16 kHz PCM16 frames ─────────────► /ws  (server.voice_socket)
  canonical JSON events + audio segments ◄─ LockedSender
                                                    │
                                     ListenerSession (one per connection)
                                       FrameBuffer → vad.EndpointDetector
                                       level gate (only when the person's filter is on)
                                                    │ utterance
                                       STT (voice_providers, provider setting)
                                                    │ transcript
                          empty / self-echo → emergency gate (emergency.py)
                          → paused: resume words only → fragment / filler /
                            calibration / language admission (every drop: speech.dropped)
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

The waiting cue ends locally as failed if its transport disappears, logging
only the exception type; it leaves no unobserved task failure.

Only the Cascade path exists (`ready` advertises `pipelines:["cascade"]`).
There is no Realtime/Native transport, and no "general brain" for a session
without a protocol: a voice session refuses to start without one.

## 2. Who owns what

| Concern | Owner | Notes |
|---|---|---|
| Workflow state (step, completion, timers, pause, observations, today's values) | `curated_protocol.CuratedProtocolSession` | The only state machine. Every change — read by a front rule or proposed by the router — runs through its branches (`plan`, `apply_tool_proposal` → `_execute_turn_intent`). Today's values (`set_run_changes`, front rule `run_value_change`) come only from a person's choice or words; no router tool changes them. |
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
   `forced`). With the filter on (lane HEAR, decision 2: the experimenter's
   `speaker_filter`, a `voice_reference` measured through
   `POST /api/voice/calibration` under `VOICE_REFERENCE_MAX_AGE_DAYS` old, on
   the microphone the page names in `client.audio_ready` — `stored_voice_filter`),
   speech far quieter than the wearer's reference is dropped before STT
   (`ignores_level`, event `speech.ignored`). No voice is measured during a
   run (decision 1). Speech that starts within `CONTINUATION_JOIN_SECONDS`
   of the last end (`FRAGMENT_JOIN_SECONDS` for held or still-transcribing
   words), while that turn is unanswered and has changed nothing, is joined
   to it (`_join_continuation`: the turn is cancelled, its audio goes in front
   via `EndpointDetector.prepend_utterance`) until the kept sound reaches
   `CONTINUATION_JOIN_MAX_SECONDS` (10 s; lane HEAR2, decision 2), when the
   turn is transcribed as it stands. Every dropped sound, here or in step 3,
   goes through `_report_speech_drop` (reason code, dB, ms; event
   `speech.dropped` with `reason_ko`, the plain words; shown, never said —
   lane HEAR2, decision 4). A speech-to-text provider that fails
   (`SpeechProviderError.code`: the provider's own code or one by status,
   `timeout`, `unreachable`) is `_report_stt_failure`: the same drop event
   with the code, a metric by code, one spoken sentence per outage (lane
   HEAR2, decisions 1-2). The step timer's voice commands (`timer_control`:
   pause, resume, cancel, restart — `pause_timer` keeps the remaining time
   and sleeps the notices, `resume_timer` shifts the start by the pause) are
   read before the run's pause words and recorded as `timer_paused`,
   `timer_resumed`, `timer_cancelled`, `timer_restarted`; the reconnect
   restore (`_standing_timer`) reads the last timer event (lane HEAR2,
   decision 3).
2. The waiting cue (`cascade_filler.CascadeFiller`) starts; STT returns one
   transcript per utterance.
3. Admission, in this order (`server.run_turn`): empty and self-echo
   (`ECHO_*` constants), then the **emergency gate**
   (`emergency.recognize_emergency`, fixed reply, route
   `deterministic_emergency`). Recognized emergencies do not become calibration
   samples or wait for language confirmation. Router-first recognizes the new
   utterance before joining any held fragment; only a non-emergency is joined
   and checked again, through this same fixed-response gate. A paused run
   takes only a resume word past this point (`is_paused_resume_utterance`;
   lane HEAR, decision 7) and answers nothing else. Other turns continue through
   fragment join (both paths since lane HEAR), non-speech/filler admission, a pending
   calibration sentence and language confirmation (asked only for clearly
   English or Vietnamese words; a short Latin transcript is dropped as
   `language_unclear` — decision 8). With a protocol the ordinary
   turn language is Korean. Audio rejected before STT still cannot reach this
   text gate; no change to acoustic recognition is implied.
4. Routing, one line (AGENTS rule 3):
   - `CuratedProtocolSession.front_plan` reads the turn first and never
     waits on a model (`FRONT_RULES`: transcript quality, pause and "종료",
     yes/no to an open question, replies to an endpoint question, time left,
     "그거", repeat, cancel, completion naming the current step, start, a
     problem at the bench (`problem_help`), and the rest of the table).
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
     call the read-only `show_visual`, `clarify` and `not_for_me`. Clarify
     candidates must be source term names, and commands/completion/assent
     candidates are rejected even when a source term has that spelling.
5. Read-only roles (`multi_brain`), outside-PDF explanation, pictures and web
   lookups run after the answer and never change state. Before a Korean
   curated reply is spoken, `server._korean_first` (lane SAY) runs
   `CuratedProtocolSession.korean_first`: every source sentence the speech
   quotes in English is replaced by its reviewed or stored Korean, or by a
   reading the translation role makes in the turn (at most three, each once,
   checked by `statement_issue`, kept in the process and put on the session's
   fixture as a machine translation); with none it is said as "이 문장은 아직
   번역되지 않았어요. 원문을 화면에 띄울게요." and shown in the source. Only
   "원문 그대로 읽어 줘" (`SOURCE_READ_ALOUD_INTENT`) reads the English. No
   workflow state is involved. Lane KO2 takes the same path
   (`server._translate_in_place`) for a source sentence the reply only shows,
   for a purpose answer whose stored Korean is of an older translation style
   version (`_fresh_purpose`), and, after a fixture state is sent, for the
   current and next step sentences the screen shows without Korean
   (`_queue_screen_korean`: a checked reading goes on the session's fixture
   and the same state is sent again as `translation_update`; meanwhile the
   state's `translation_missing` and the screen's
   `next_step_translation_missing` say so). Last, every step label is said
   and shown once (`CuratedProtocolSession.single_step_labels`; the source
   quote is left as written), and `reply.complete` carries the words said
   (`speech_text`), which the new screen's caption shows.
6. Sentence TTS streams through generation-aware audio segments;
   `turn.route_decision` carries a compact projection (with a `router`
   block when the router ran); `turn.done` and `playback.completed` close it.
   The curated reply's speech is cut by `server.speech_sentences`
   (`brain.SentenceChunker`'s sentence ends, cut from the cleaned text only
   where a space follows; a piece that `clean_speech_text` would change on
   its own is joined to the one before, so the pieces are exactly the whole's
   words). The whole is remembered once as said (echo check); the first
   sentence is synthesized before playback starts, and `_say_answer` sends
   it as segment 0 after the reply's events, then makes and sends each later
   sentence while it plays, checking `is_current(turn_id, generation)` before
   each request and each send. A barge-in cancels the rest; the turn's router
   history, pilot row and workflow metric are still kept. A later sentence
   that cannot be made ends the answer there (error signals, the turn still
   ends through `playback.ended`). `audio.complete` and `turn.done` count the
   segments; an outside-PDF or web explanation is the segment after them.
   The screen text is not cut. The greeting, timer notices, clarifications
   and the emergency reply are still synthesized whole.
   `server.synthesize` keeps a sentence's audio (`SENTENCE_AUDIO`, bounded
   by entries and bytes, keyed by the speaking lab's organisation id
   (`_speaking_organization`, from the request or voice connection's
   principal; empty with no workspace), provider, model, voice, language and
   the cleaned sentence) only inside `sentence_audio_kept`: the curated plans
   `_speech_kept` allows (protocol readings and fixed server sentences, not
   records, repeats, answers to questions, model-written speech or the
   experimenter's words) and the greeting. The emergency reply never is.
   The page keeps every segment one turn played, in order, for "다시
   들려줘" (`keepReplayableAudio`, at most 120 s; the replay turn's own
   reply is not kept). A step's Korean begins with its own "N단계: "; the
   previews, range summary and lab-equipment reading leave that prefix out of
   `speech_text` only.

The front rules and proposal checks share
`intent_fences.is_question_or_speculation`: Korean question/speculation
endings and question marks cannot authorize observation, completion, timer,
resume or anomaly mutations. Existing read-only answers still run; an open
server question is neither consumed nor refreshed by asking a question.
Its original turn ownership and stale fences remain. An explicit literal
record command may preserve uncertain words, but those words cannot authorize
a workflow change. Notebook questions survive pause/resume and expire on
step or revision movement rather than reopening when the user returns.

Cancellation joins already started durable writes before rollback or a new
bench control. If the worker committed, memory keeps that committed state
and the corresponding report instead of restoring an older checkpoint. A
replaced voice connection is refused before planning and again inside
observation/progress transactions (`workspace_conflict`).

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
  → optional page range (the request's `page_range`): only those pages are
    sent, checked, counted for the hollow bound and searched for missed lines
  → one structured analysis (or pieces of ≤15 pages, ≤400 pages), background job
  → server-owned evidence check, requote of refused items, domain validation
  → lines no quote covers that read like an instruction (possibly_missed_places)
  → records whose words read as written to the program (program_address_flags)
  → all confirmed: analysis revision; those lines one start notice
    otherwise:     "review pending" ledger event + cards, those lines cards too
                   (never an analysis revision)
                   → decisions → registered review version vN (latest runs)
  → execution rule at read time: analysis (or review version) present and no
    execution blocker → available_for_execution
  → start wizard: start check (two key values) → safety statements →
    today's values (always passed, "바꿀 값 없이 다음" included;
    `protocol_run_changes_chosen`) → start naming that choice
    (`run_change_set_id`; applied once: `protocol_run_changes_applied`)
  → CuratedProtocolFixture for the session
```

- The catalog is append-only: uploads, OCR results, analysis requests and
  results, review decisions, review versions, start-check answers, a
  person's own name for a protocol (`protocol_title_renamed`, lane RX1: the
  latest one is the title everywhere it is read) and
  "확인함" are events in `protocol_workspace.sqlite`. A reanalysis adds a
  revision; a run continues on the revision it started with
  (`load_fixture_for_revision`).
- PDF text extraction suppresses image-byte encoding in raw text output and
  uses PyMuPDF image-info for geometry. It keeps the same scientific strings
  and image boxes; the bounded child-process timeout is unchanged (default
  30 seconds). Launchers show the effective limit; long-document success is
  still conditional on completing within it.
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
- Catalog executability and personal start confirmation are distinct.
  With `VOINEY_LAB_START_CHECK_ENABLED` enabled (default), `session.start`
  also calls `start_check.StartCheck.readiness`: the current principal must
  have answered the current key-value questions and acknowledged safety for
  the exact source revision, analysis and review version (`StartTarget`).
  `GET /api/protocols/{id}/start-check` supplies that target; after the values,
  `POST /api/protocols/{id}/start-check/safety` records it as
  `protocol_start_safety_acknowledged`. Even a source without numeric values
  needs the safety receipt. Another account's receipt, an older target or an
  earlier correct answer followed by a wrong one grants nothing.
  Refusal is `session.configuration_required` with a reason and `message_ko`,
  before creating workflow, timer or report-start state. The screen shows
  that reason and returns to the required wizard step. When the setting is
  false, the value and safety receipt checks are disabled together.
  Recovery uses the original execution revision's own receipt; no missing
  historical receipt is invented. `workflow.resume` needs an already started
  experiment and the current configuration id and generation.
- Review cards never rewrite a safety statement (`_FIXABLE_KINDS` has no
  `warning`).
- Today's values (lane EASY): `start_check.changeable_values` lists the time,
  temperature, speed, volume and concentration values of each step's own
  sentence, never one in a warning or limit sentence; `check_run_change`
  takes only the same unit family; a value below half or above twice the
  source's (`run_change_is_large`) is refused until the screen confirms it
  (`confirm_large`). Every start passes the step: `session.start` names the
  choice (`run_change_set_id`); with the start check on, a new run that names
  none is refused `run_changes_required`, and a choice that is another
  person's, used, or no longer the fixture's is refused `run_changes_stale`.
  Nothing chosen earlier applies on its own.
  During the run `CuratedProtocolSession` changes or puts back a value
  (confirm_mode readback; asked first in 바로 확인 or noisy mode, and in
  every mode for a large value), runs the
  step's timer for a changed time, says the value at the step and puts it in
  `state()["current_step_run_changes"]`. The record (`run_value_changed`,
  `run_value_reverted`, `session_started.run_changes`) feeds the notebook's
  "원문 → 실제"; the workspace events feed a recovery; "지난번처럼" reads the
  owner's last record (`ExperimentReportStore.last_run_values`).

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
  unnumbered, value-free instructions (conditional notes, storage).
- Since 2026-10-13 nothing on the live path reports a repeat instruction or a
  value the model left out: `assess_readiness` keeps those reason codes, but
  no caller feeds them. `hollow_analysis_check` (≥ 20 source steps, < 25 %
  kept, refuses) and the prompt are the blocking completeness checks. Some
  decode-only names (`page_coverage`, `TextVerification`,
  `SOURCE_TEXT_CROSS_CHECK_*`) remain so that old payloads still load.
- Lines the model never kept (human decision 2026-10-23):
  `possibly_missed_places` takes each page's text, sets aside every quote's
  characters (any record's, wherever printed; of several equal places, the
  whole lines), the furniture lines the page-end rule skips
  (`_PageEdges.is_crossing_furniture`), a cover page, a references list and
  copyright, licence, DOI and contents lines, and keeps the lines left that
  read like an instruction. The shapes and their word lists are one constant
  block in `experiment_protocol_analysis.py` (`MISSED_*`): a numbered or
  bulleted sentence, an English imperative (after an opening clause too), a
  Korean "~한다/~하세요/~할 것" ending. A value with a unit alone and a label
  before no sentence are recorded but show nothing (they were most of the
  false alarms measured). A review-pending analysis gets one card per place
  that no other card asks about -- a failed piece's pages and a refused
  quote's suggested lines are that card's (`possibly_missed_flags`, at most
  `VOINEY_LAB_REVIEW_MAX_ITEMS`, put together by page and then by runs of
  pages; never counted toward the bound that re-analyses or refuses); a
  "단계가 아니에요" changes nothing in the version it is registered with, and
  places nobody answered stay its start notice; a passed analysis stores the places in its
  `protocol_analysis_ready` event and `review()` adds one non-blocking notice
  (`source_text_possibly_missed`). It is no completeness guarantee: an
  instruction in none of the shapes, or one another record's quote covers,
  is not found.
- Words written to the program (lane TIDY, 2026-10-25): a step, an action or
  a statement beside them (safety statement, note, tip, completion
  criterion, condition, observation, before-start item) whose words have a
  shape of `PROGRAM_ADDRESS_SHAPES` in `experiment_protocol_analysis.py`
  (they address the program, order the instructions before ignored or every
  step marked done, or use a program's own words such as "system prompt")
  gets a card (`program_address_flags`, reason `addressed_to_program`),
  added after the card bound like the possibly-missed lines, so an analysis
  that otherwise passed waits for review. [빼 주세요] is the `clear`
  decision -- a step or an action leaves the version that runs -- and
  [실험 지시예요] keeps the words as the source's. A safety statement is
  never cleared on this ground (rule 5): its card offers confirm or a
  re-analysis. The words were never obeyed before either; the card keeps
  them from being read aloud as a step. Word lists only, no model.
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
- Help with a problem (`knowledge.py`, front rule `problem_help`): a problem
  said or asked at the bench (`intent_arbitration.problem_reading`; label
  `RequestIntent.TROUBLESHOOTING`) is answered read-only. A problem said as
  having happened is first recorded as an anomaly (the existing read-back);
  one asked, or the step's repeat-until state "not yet", is not recorded; the
  step's own endpoint words and move commands stay with the endpoint readers
  and the completion gates. The plan carries a `problem_help` marker; the
  server (`_with_problem_help`) searches the tiers at once
  (`search_tiers`, 1.5 s each) and puts the help after the reply:
  ① `ProtocolKnowledge` -- the step ± 1 and its section's notes, expected
  results, warnings, repeat/branch conditions and unclaimed troubleshooting
  page lines, the source's words with page and step, said by their checked
  Korean reading; ② `HistoryKnowledge` -- `ExperimentReportStore.earlier_reports`
  (same protocol, organisation and owner; a lab-only record has none), the
  same step's anomalies and observations and what followed them; ③ a public
  source (`session.public_knowledge`, the tier lane KNOW loads at start, in
  decision 6's form -- see the next item) and, with the
  supplemental role on and no safety category, possible causes
  (`explain_possible_causes`) said after the answer by `_queue_problem_causes`,
  which holds ④ the closing sentence (`knowledge.CLOSING`) until then and says
  ④ alone when ③ is late, fails or fails `possible_cause_violations`. Both
  searches are in-process SQLite FTS5 over prepared terms (values with units,
  English stems, Korean words without particles, concept terms joining the
  two languages); nothing leaves the process. The help is composed from the
  sources' words and fixed sentences only (`compose_problem_help`, checked by
  `problem_help_violations`) and recorded as a `problem_help` event with its
  places, not its text. A source that cannot be used raises
  `KnowledgeUnavailable` (its setting) and is skipped; none replaces it.
- Public material (lane KNOW, human decision 2026-10-24):
  `public_knowledge.PublicKnowledgeSource` is the `public` tier of the
  knowledge contract — one Moss index of licence-checked public pieces
  (public domain, CC0, CC BY, CC BY-SA; `data/public_knowledge/sources.toml`,
  built by `scripts/public_knowledge_pipeline.py`), loaded once at start by
  `server._start_public_knowledge` and queried in process. A Korean
  question is joined by the English names of its words
  (`data/public_knowledge/glossary.json`, Wikidata labels, CC0) before the
  query. Hits come best source grade first (A public institution /
  peer-reviewed, B open textbook, C wiki) with source, licence and address;
  what may be said from them is `answer_checks.public_reference_violations`
  (only the piece's numbers, a fact not an instruction, no "continue", no
  value against the source's, the "공개 자료 · 일반 참고 — <출처>" mark added
  by the server). It is a general reference beside the source and never a
  value or a safety statement; off, or without `MOSS_PROJECT_ID` /
  `MOSS_PROJECT_KEY`, or after a failed load, this tier alone is off by
  setting name. Wired into the problem help (2026-10-25, after lane HELP's
  merge): `voice_socket` sets `session.public_knowledge` from
  `app.state.public_knowledge`; `_with_problem_help` composes the protocol
  and history hits with `compose_problem_help` and appends the public ones
  through `_with_public_reference` / `external_references.public_reference_part`
  -- only pieces sharing a word with the problem (the tier marks each hit
  with `shared_term` of the glossary's terms and the problem's own Latin
  words; the index always returns its nearest pieces) -- (the mark, title and place, the piece's words, address and licence on the
  screen; said, the mark and a short same-language fact or where to see it;
  every line naming the source is quoted, so `problem_help_violations` judges
  only the fixed sentences). With the supplemental role on, the spoken form
  waits for ③, where `_queue_problem_causes` runs `paraphrase_public_reference`
  beside the possible causes and says the checked paraphrase, or the fixed
  form when it is late or refused, before them and ④.
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
| Workspace | `<workspace>/commercial_workspace.sqlite` + `evidence/…` | 12 | `workspace_store` | session events, completed steps, observations, evidence, sentence translations and glossaries (each with its translation `style_version`) — SQL triggers refuse UPDATE/DELETE |
| Experiment record | `<protocol dir>/experiment_reports.sqlite` | 2 | `experiment_reports` | events by stable key (`INSERT OR IGNORE`); no triggers — enforced by code; prose is replaceable |
| Accounts | `<workspace>/accounts.sqlite` | 1 | `accounts` | no (sessions end; only hashes stored) |
| Pilot metrics | `<workspace>/pilot_metrics.sqlite` | — | `pilot_metrics` | rows of numbers, codes and times |
| Safety catalog | path in `VOINEY_LAB_SAFETY_CATALOG` | 2 | `scripts/ingest_safety_documents.py` | read-only at runtime |

- Stores are separate files; cross-store operations use exact identities and
  fail closed; there is no distributed transaction.
- Source metadata is appended as `protocol_source_metadata` during
  registration. Startup backfills missing metadata once. Lists filter tenant
  ownership first, then read metadata, analyses and ledger summaries without
  extracting PDFs. Verified immutable analysis records are cached by source,
  revision and actual record bytes; changed bytes are validated again.
- Report schema 1 migrates to 2 on open; startup/access backfills ownership
  from the exact workspace session. A lab-only legacy record allows same-lab
  administrators to read/export and append PI confirmation, with no owner
  write permission; an unscoped one stays inaccessible. Historical events and prose are
  preserved. Rollback requires the matching pre-migration backup (migration
  details in `docs/MIGRATION_NOTES.md`).
- Observations are `observation_only` and evidence files `not_interpreted`:
  neither becomes protocol knowledge.
- The workspace keeps tables that nothing writes any more (approvals,
  adaptations, connectors, dry-lab, ELN write-back); see
  `docs/MIGRATION_NOTES.md`.
- In-memory only: runtime metrics, web photos, drawn SVGs, report-prose job
  state, the speaker's level reference, and the public knowledge index Moss
  holds after the start (its disk cache sits beside the protocol catalog in
  `public-knowledge-cache/`; the cloud copy is the source of truth).

## 7. Identity and accounts

`VOINEY_LAB_AUTH_MODE=dev` (default) gives every request the one development
identity. `accounts` puts the `commercial_identity_boundary` middleware in
front of every `/api` path except `_ACCOUNT_OPEN_PATHS` and in front of `/ws`;
it needs the workspace. In `operational` usage scope, `identity.py` demands
OIDC bearer tokens. `_account_is_admin` is the one administrator judgment
(pilot metrics, "확인함", disabling accounts). App origins
(`VOINEY_LAB_APP_ORIGINS`) may log in with a bearer token.

Report reads/exports and evidence reads require the owner or a same-lab
administrator; value confirmation, correction, feedback and prose regeneration
require the owner. An administrator may append PI confirmation to a same-lab
record, without granting execution. Other-lab/unauthorized reports appear
absent. The full permission matrix is in `.agent/security_rules.md`.
Previous-run comparisons, start-check answers and daily run numbers are scoped
to the same owner, lab and protocol; unknown owners receive no guessed number.
Login and current-password failures are counted by (normalized name, device):
five failures in fifteen minutes lock that name on that device, thirty from
any devices lock the whole name, each for fifteen minutes. The device is a
random HttpOnly cookie (`voiney_device`, one year) the browser receives with
`/api/auth/session`; a listed app origin sends it in the request body; a
request without one is the one unknown device. Only digests are stored.
Logout, password change, disable and deletion close all that account's open
voice sockets; the HTTP session scope still follows the account operation.
Account erasure drains in-flight voice report writes before inventory/deletion
so a late worker cannot recreate a deleted report.

An account administrator sees only that lab's pilot aggregates; server-token
and offline aggregation retain their whole-store scope. The development
fixture follows the same tenant visibility as other catalog items. API
documentation endpoints require login in accounts mode.

## 8. Connection loss and timers

`server._send_heartbeats` beats once a second; `static/connection.js`
declares loss after 2.8 s of silence, makes the page read-only, pauses the
microphone, keeps a copy of the step timer on the server's clock and
reconnects (1, 2, 4, 8, then every 15 s) with `session.start` carrying the
experiment id and a `reconnect` block. The server restores the same
experiment (`_restore_lost_connection`, `_restore_standing_timer`), delivers
missed timer notices and records a content-free `connection_restored` event.
Recovering a paused or blocked run preserves that state even without a
`reconnect` block; only explicit current voice/bench resume can lift it.
Direct HTTP experiment `/transition` returns 409 `voice_control_required`
after access/input checks, with no workflow change. Unknown socket controls
return a recoverable error and preserve the connection and checkpoints.
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
`voice-core.js` (the socket, the microphone, the answer's audio, the
server's clock, the screen lock — loaded just before the page's own script
and shared with the new screen, lane RX2), `connection.js` (loss and
reconnection), `feedback.js` (end-of-run rating) and `mic-capture-worklet.js`
(capture) load with it; `native-bridge.js` does
not yet. `admin-metrics.html` is a separate page.
The upload screen's public Drive/Docs form calls `/api/protocols/import-link`
and shares the file upload's response handling, analysis/OCR polling and
result display. Link errors display server text through `textContent`.
Import is disabled during registration or a running session; stale-generation
responses are discarded. Sheets reagent-table attachment remains API-only.

`#/protocols` is the list, `#/protocols/<id>` its own detail screen, and
`#/upload` follows pick → progress (elapsed time/current work) → result.
While the page is visible and signed in, the catalog re-reads
`/api/protocols` quietly (`scheduleCatalogRefresh`): every 5 s while an entry's
analysis runs, every 30 s otherwise, and at once on a tab change, on
`visibilitychange` back to visible and when the start sheet opens; it stops
while hidden. A quiet refresh keeps status lines, skips an unchanged list,
keeps the list on failure, drops answers older than the one drawn, and does
not redraw the start sheet while one of its inputs has focus. The
start sheet and the start ask the server once more
(`recheckProtocolRunnable`) before refusing a protocol as not runnable.
Detail groups analysis, source comparison, reagent tables, review history
and reanalysis in expandable sections. `#/records` lists server-provided
experiments; `#/records/<id>` opens one timeline and reads
`GET /api/workspace/experiments/{id}/reports` for existing report summaries.
Experiment and report read gates both apply, no report is generated by this
read, and the screen offers Word/Markdown/JSON/CSV for each report. Failed
loads can be retried with record refresh. Another owner's experiment is
read-only, with capture/restart and report write controls hidden. Only the
server's run number is displayed. The experiment tab's start
card follows the selected/resumable experiment; every start goes through the
wizard's final button. Clicking end waits for the server's ended state before
showing the after screen, and ended experiments are not offered for resume.

All server URLs go through `serverOrigin`, `serverUrl`, `serverSocketUrl`
and `serverFetch`. The current `serverOrigin()` returns an empty string for
same-origin requests. A store-app origin still needs wiring here; these functions add no
workflow authority. An authenticated request's session-expiry 401 calls
`signOutOnScreen`, as does logout: clear the used invite, stop microphone and
connection state, and show login. Own authentication failures (such as a
wrong current password) are handled by their account screen. A 1008 socket
close also rechecks the account before continuing reconnection. Home resume
candidates are the current owner's runs; while running, home offers return
only. Start failures keep the sheet open with a reason, and a failed
key-value load stays on its step with retry. Source-check/registration
blockers remain visible. Settings opens as a sheet over the run; account
erasure asks for the password again and returns to login on success.

**The new screen** (`web/`, React 18 + TypeScript + Vite + TanStack Query,
Radix dialog, lucide icons, Pretendard bundled) is built by `web/build.sh`
into `static/app/` (git-ignored) and served at `/app/` by three routes in
`server.py` (`new_screen`, `new_screen_asset`, `new_screen_without_slash`);
`root_screen` sends `/` there only with `VOINEY_LAB_NEW_UI=true` and a
build present, else serves `index.html` as before. The launchers call
`web/build.sh` and go on without the new screen when node is missing or the
build fails. It keeps the old hash routes (`#/home`, `#/protocols`) and has its own
(`#/upload`, `#/protocols/<id>/analysis`, `#/protocols/<id>/review`,
`#/protocols/<id>/changes` — lane RX1; `#/start`, `#/start/<id>`,
`#/experiment`, `#/settings`, `#/welcome` — lane RX2); a route it has not
taken over (`#/protocols/<id>`, `#/records…`) is opened on the old screen at
`/index.html`. Its experiment screen does not reimplement the voice: it
loads `/voice-core.js` and `/connection.js` as classic scripts and
`web/src/voice/engine.ts` defines the names they call
(`VOICE_CORE_HOST_NAMES`, connection.js's per-turn maps and renderers), reads
the session's events (the audio and fence conditions as index.html's
`onMessage`; `tests/test_lane_rx2_voice_core.py` compares them) and draws the
server's protocol state through `runModel.ts`. The start flow, the settings
and the first guide use the same HTTP routes as the old start sheet and
settings.
It holds no workflow state: the home's one card and the list's chips are
read from `/api/auth/session`, `/api/protocols`, `/api/protocols/{id}`
(`analysis_run` for a row being analysed), `/api/workspace/experiments` and
`/api/workspace/session`, re-read every 5 s while a protocol is analysed and
every 30 s otherwise, at once on focus, never while hidden. A 401 on a
signed-in read clears the lists and shows the login. Every sentence it
shows is in `web/src/copy/ko.ts`; server sentences are shown as sent.
The upload registers the file (`POST /api/protocols`), then asks which
pages to analyse (`GET …/review-draft/source-pages` for the count,
`…/source-pages/{n}/image?size=thumb` for the pictures) and starts the
analysis itself (`POST …/analysis?first_page&last_page`); a source whose OCR
the upload started, or one already analysed, skips the question. The
confirmation screen reads `…/review-draft` and `…/source-pages/{n}` (each
line's `box` in points and `image_dpi`, so the band on the page image is
placed by dpi/72), posts each decision (`…/review-decisions`) and, when
every card is decided, registers the version (`…/review-versions`).
Today's values read `…/start-check/changes` (`steps` with `text_ko`, values
with `start`/`end`) and post the choice; the server's 422 for a large value
is asked once more with `confirm_large`. A person's own name for a protocol
is `POST …/title` (`protocol_title_renamed`, append-only). An odd read
title (a cover line, a page mark) is shown as the file's name on the screen
only (`web/src/lib/protocolTitle.ts`); the server's title stays.

Tablet rules apply only under `(pointer:coarse) and (min-width:721px) and
(min-height:600px)`: 44 px targets, a two-column landscape run (step |
timer, figures in a sheet), far-reading and glove tokens (`--read-far-*`,
`--target-glove`, `--gap-glove`). The start press itself asks for the screen
wake lock (held while the start is pending, `keepScreenOnForStart`) and
resumes the playback context (`primeRunStart`); a context the browser still
holds back shows `#audio-unlock`, and `client.audio_ready` is sent once,
after that touch. A refused microphone names the device's own setting
(`micBlockedWords`). Phone and desktop layouts are unchanged.

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
| Safety scope needs a catalog that is missing or unusable | `server_config` refuses; `/readyz` returns 503 `safety_configuration_invalid` with setting names only |
| Usage scope missing/blank, or malformed start-check/workspace boolean | Configuration error naming the setting; no silent scope/boolean fallback |
| `VOINEY_LAB_PUBLIC_KNOWLEDGE_ENABLED` without `MOSS_PROJECT_ID`/`MOSS_PROJECT_KEY`, a malformed switch, or an index that fails to load | That tier alone is off; `ready.research_capabilities.public_knowledge` and `/readyz` say `unavailable` with the setting name; the server starts and no other provider is tried |

## 12. Audio constants

The capture rate is one constant, `audio.SAMPLE_RATE` (16 kHz);
`SAMPLES_PER_FRAME` and `FRAME_BYTES` derive from it and `vad.py` passes it to
the VAD. `webrtcvad` also accepts 8, 32 and 48 kHz. The browser resamples
once (`resamplePcm16`). The output path holds its own `16000` literals (the
`ready` event, TTS output formats, the browser's buffers), so changing
capture and playback are separate edits. No provider's STT rate requirement
is recorded in this repository.
