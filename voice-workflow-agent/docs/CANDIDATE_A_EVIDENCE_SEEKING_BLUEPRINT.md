# Candidate A evidence-seeking hardening blueprint

This blueprint records the implementation boundaries adopted from the
2026-08-10 and 2026-08-11 real-voice Acceptance failures. Candidate A remains a
development-only, `analysis_required` curated fixture. Cascade remains the
authoritative workflow runtime; Native is comparison-only.

## Confirmed root causes

- Completion-only reports are absent from the deterministic curated intent
  grammar. They therefore fall through to related-question retrieval even
  though the existing `NEXT` operation already provides an atomic,
  checkpointed transition.
- Detail requests recognize only a narrow subject-first word order and the
  `FULL_DETAIL` route normally repeats one localized source sentence. The
  planner does not combine the other verified facts for that step.
- The verified Step 7 expected-result fact already contains the transparent
  endpoint, the usual two Solution A/B cycles, and the page-5 visual
  reference, but no deterministic expected-result explanation admits all of
  that evidence.
- Related safety vocabulary is incomplete. Even when related routing occurs,
  the terminal miss is a generic `not_found` response instead of a truthful
  statement of the protocol fact boundary and the unavailable evidence tier.
- Context resolution is a one-off Solution A rule tied to Step 3; it does not
  maintain a bounded recent-entity or clarification target.
- Playback complaints have no deterministic route or replayable-audio owner,
  so they become off-topic.
- The STT path previously discarded optional confidence, alternatives, and
  no-speech metadata. The normalized contract now retains those fields only
  when the provider supplies them; absent fields remain `None` rather than a
  fabricated score. Conservative reviewable mismatch patterns remain a fallback
  and explicit stop/replay commands retain precedence.
- `CuratedProtocolFixture.visual_for_step()` unconditionally synthesizes a
  text-heavy SVG when no verified source crop exists. This creates the large
  automatic schematics and carries extraction glyphs into another rendering
  path.
- Derived presentation normalization is applied inconsistently. Raw fixture
  and PDF bytes must remain unchanged; normalization belongs only at display
  and TTS boundaries.
- AMBIC, HPLC water, and solution-composition questions were rejected before
  the protocol entity/adjacent-step evidence layer because the related-term
  gate did not recognize those scientific entities.
- The local catalog's approved flag was treated as sufficient even for its
  explicit `demo` scope. Retrieval success therefore meant “rows returned,”
  not “a cited claim was admitted for this protocol and question.”
- The product had only source-crop and generated-illustration paths. It had no
  separate rights-aware web-image operation, so a request for a real example
  could not be represented truthfully.
- The existing safety handoff Tool creates a report per confirmed model Tool
  call. It is not a durable, idempotent experiment-session record and cannot be
  used as an automatic report trigger.
- Native transcript/audio events sometimes used the mutable current turn after
  a new user onset. The browser then replaced the interrupted card's answer;
  its audio scheduler also inserted a fixed 10 ms gap whenever a drained queue
  restarted, conflating source gaps with client-induced gaps.

## Adopted architecture

1. **Intent precedence.** Explicit stop; start/resume; current/repeat/replay;
   completion-only or completion-and-next; other state operations; explicit
   visual request; grounded explanation; contextual entity question; related
   safety/knowledge question; clarification; off-topic. Deterministic matches
   own mutations. A bounded classifier seam may classify read-only intent but
   cannot authorize state changes.
2. **Compound controls.** A structured intent records reported completion,
   requested transition/follow-up, language, and confidence source. Both
   completion-only and compound forms call the same existing atomic transition
   once; no separate `complete` and `next` calls are composed.
3. **Evidence ladder.** Active protocol facts are checked first, then approved
   active local documents, then one domain-allowlisted xAI web search when
   explicitly enabled. External evidence is supplementary and never changes
   the active protocol.
4. **Approved ingestion.** The existing manifest-driven SQLite catalog remains
   the approval authority. Stable document/chunk identities, approval/version
   filters, and deterministic local retrieval are retained. Moss remains an
   optional adapter and no protected document is uploaded implicitly.
5. **Answer planning.** Read-only explanations admit explicit protocol facts
   into a typed display plan. Korean speech stays concise; written output keeps
   supported points, exact original excerpts, source pages, limitations, and
   source-tier labels. Missing facts are named rather than invented.
6. **Bounded session context.** Each curated session tracks a small set of
   recently presented verified entities, the last answer/replay owner, and one
   pending clarification. It is reset on protocol/session reset and is never
   rebuilt by stale async results.
7. **Search boundaries.** Web search is a feature-gated internal adapter, not a
   workflow mutation tool. HTTPS and configured domains are revalidated;
   results are bounded, deduplicated, cited, and treated as untrusted data.
8. **Visuals.** A verified source crop is the only automatic visual. Otherwise
   the UI shows a compact no-original state. Grok generation is allowed only
   after an explicit visual intent, uses a source-bound server specification,
   returns after the text/TTS path has begun, and patches only its originating
   generation. Failure does not create a schematic.
9. **Turn ownership.** Search, replay, and visual state stays attached to the
   existing `(configuration, turn, generation)` identity. Duplicate and stale
   late events are ignored. Operations are reported truthfully as deterministic
   server operations or internal services, not fabricated model tools.
10. **Conversation UI.** The existing fixed-height chronological chat viewport
    and scroll-anchor policy are preserved. Dense evidence remains in
    expandable Turn details; routine visuals are not duplicated at uncontrolled
    height.
11. **Latency and recovery.** Route, local retrieval, answer-ready, first-audio,
    and visual-ready boundaries remain separate. Slow evidence acquisition may
    emit one truthful Turn-local progress cue. Replay and unreliable-transcript
    recovery are read-only and cannot stop or advance the procedure.
12. **Evidence admission.** Approval is necessary but insufficient. Runtime
    guidance rejects `demo`, `test_only`, fictional, and non-operational records,
    then requires protocol/general scope plus entity and question-dimension
    support. A rejected local tier may escalate to the enabled domain-restricted
    web tier; it is never displayed as a successful source.
13. **Visual roles.** Original protocol media, authoritative web-image source
    pages, and AI-generated illustrations are separate internal operations. A
    web result is exposed as a source link unless copying/display rights and
    bytes can be verified; it is never relabelled as protocol evidence.
14. **Experiment reports.** A feature-gated SQLite event store keeps one report
    per Candidate A procedure session. Server-owned start, committed step,
    anomaly, stop, and finalization triggers use idempotency keys. The export
    contains protocol identity, events, blockers, and source tiers—not prompts,
    reasoning, credentials, or raw audio.
15. **Native ownership.** Provider response/item identity owns every Native
    text and audio delta. Barge-in preserves received display text, reports
    played duration for provider truncation, rejects late events, and creates a
    new card only for committed new content. One AudioContext schedules chunks
    monotonically without an artificial per-drain delay; provider arrival gaps
    and client underruns are separate metrics.

## Reference adoption matrix

| Reference | Pattern | Decision | Reason / dependency |
|---|---|---|---|
| OpenAI voice-agent guidance | Chained STT → application logic → TTS | Adapt | Matches the existing deterministic Cascade authority; no new dependency. |
| OpenAI realtime evaluation guide | Preserve stage traces and turn real failures into regressions | Adapt | Extends the existing offline fixture/evaluation style; no new dependency. |
| Google ADK session state | Bounded structured short-term session state | Adapt | Implement locally in `CuratedProtocolSession`; no framework dependency. |
| xAI Responses web search | Domain-restricted read-only evidence lookup | Adapt existing adapter | Uses the installed OpenAI-compatible client; live use remains feature-gated. |
| xAI image generation | Base64, source-bound instructional image | Adapt existing adapter | No automatic generation; live use remains feature-gated. |
| xAI image search + `view_image` | Distinguish real examples from generated illustrations | Adapt behind a rights-safe source-link boundary | No remote-byte copy or hotlink without validated rights; no new dependency. |
| OpenAI Realtime client events | Cancel, clear output audio, and truncate unheard context | Adapt provider-equivalent semantics | Current provider events differ; browser sends the repository's validated truncate control. |
| Event-sourced reporting | Idempotent procedure-session record | Implement as a repository-native SQLite service | No model tool or new framework; runtime file remains ignored. |
| `civiliangame/meet_AGI` | Separate audio transport, session state, recovery, and product UI concerns | Adapt selected patterns | Do not copy its agent framework or multi-agent orchestration. |
| Google Custom Search JSON API | New search provider | Reject | Closed to new customers and on a published transition path; no dependency added. |
| YouTube `search.list` | Video discovery metadata | Reject as evidence | Search metadata is not validated laboratory content or a source transcript. |

## Failure boundary

No LLM, retrieved document, webpage, image, replay button, or UI event can
advance, stop, reset, approve an observation, or resolve Steps 7, 9, or 20.
Provider-disabled paths must remain useful and truthful without pretending a
search or image call occurred.

## Week 5 direct-answer and interpretation contract

The 2026-08-12 hardening pass keeps the same authority boundaries while moving
scientific questions away from a raw-evidence-first presentation:

- The immutable finalized STT text is retained for audit. The xAI batch request
  sends documented `language=ko` bias and repeated, bounded `keyterm` fields
  built from the current/adjacent steps plus a small critical vocabulary. It
  does not rely on undocumented confidence or alternative fields.
- Scientific normalization returns an ordered `requested_entities` collection.
  It can transparently repair the reviewed `ANBI-C`/`anbi` → `AMBIC` and
  `Jel Tug`/`제트 플러그` → `gel plug` variants; it does not fuzzy-rewrite
  numbers, units, step identifiers, or Solution A/B.
- Completion and next-step meaning are detected compositionally. Negation,
  criteria questions, quoted speech, hypothetical language, and ambiguity are
  classified before the single server-owned transition. A Turn can advance at
  most one step.
- A `SourcePlan` identifies which source class may support each requested
  dimension. An `AnswerEnvelope` leads with a plain Korean definition and
  current-protocol relationship; exact PDF facts and excerpts remain supporting
  evidence. Compound questions retain every requested entity in user order.
- Entity-specific image requests are first-class read-only intents. The planner
  resolves the entity before source-crop, rights-safe source-page, or explicitly
  requested generated-illustration selection. It never claims a visual was
  displayed when no verified visual outcome exists.
- External research uses one domain-restricted, cancellable Responses stream.
  Telemetry records first event/text, tool start/end, total time, terminal
  status, completed search-tool count, and citation admission without payloads
  or credentials. A URL without a completed search event is not success.

Offline regression commands:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m unittest \
  tests.test_candidate_a_research_hardening \
  tests.test_approved_references \
  tests.test_curated_protocol_cascade \
  tests.test_server_helpers -v
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B scripts/evaluate_candidate_a_hardening.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B scripts/evaluate_candidate_a_grounded_qa.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m unittest discover -s tests -v
```

The evaluator dataset contains the exact Week 5 real-voice transcripts and
forbidden mutation outcomes. Automated execution uses provider fakes and must
report zero paid calls. Live Acceptance remains a separate, bounded operator
step using the documented domain profile and budgets.

## 2026-08-13 authority and terminal-state refinement

The following boundaries are **CLASS-EXPLICIT** principles: deterministic
workflow state is separate from explanatory model output; tool/provider work is
bounded and observable; evidence authority is visible; and slow work remains
owned by its originating Turn. The concrete regexes, event names, SQLite store,
deadline values, and two-panel desktop layout are **PROJECT-ENGINEERING**
decisions for this FastAPI/Cascade implementation.

- A bare `다음 단계로 안내해 줘` or `Guide me to the next step` creates a
  server-owned `PendingCompletionConfirmation`. It is bound to configuration,
  step ID/index, workflow revision, requesting Turn, and generation. It causes
  no mutation or report event. Only an immediate valid affirmative response can
  reuse the existing atomic completion transition; a negative or incompatible
  response clears it. Reset, stop, transition, version/configuration change,
  and blocker handling invalidate it.
- Completion/anomaly wording is now downstream of the existing report-store
  call. A completion persistence failure restores the pre-transition curated
  checkpoint and returns a blocked, truthful response. Successful persistence
  precedes both the user-visible acknowledgment and TTS. Replayed event keys
  remain idempotent; a TTS failure after persistence does not undo the report.
- Scientific follow-ups first reuse ordered normalized entities and the bounded
  most-recent related entity. `bicarbonate`, destaining purpose, and keratin
  contamination therefore enter the read-only source-planning path rather than
  generic off-topic handling. Contamination/prevention is classified as a
  safety dimension, not a model-only conceptual gap.
- Every begun research operation emits at most one terminal result with the
  exact `(configuration, turn, generation)` identity. Public terminal classes
  are success, failure, timeout, cancelled, superseded, and unavailable. Stop
  and a newer accepted Turn terminally close the old operation; the browser
  records that terminal state and rejects every later result for the same key.
- `SUPPLEMENTAL_MODEL_KNOWLEDGE` is an optional, separately configured source
  class after all admitted authoritative tiers fail. It uses no search tool,
  produces no citation, and is visibly/spokenly qualified as general model
  background without admitted authority. Safety, preparation, substitution,
  quantities, conditions, completion criteria, and workflow operations are
  categorically ineligible.
- At desktop widths the Procedure and Original/Related Visual panes form the
  primary two-column region. Turn history follows below in a bounded viewport.
  The primary Turn answer remains concise; original excerpts, citations,
  server operation, tool/service records, and latency stay accessible through
  expandable evidence/diagnostic details.

The bounded live isolation matrix on 2026-08-13 made two xAI requests. A
text-only `grok-4.6` Responses stream completed (first event 1.344 s, first text
2.329 s, total 3.218 s). A raw PubChem-domain web-search stream reached 15 tool
events but produced no completed response or admitted citation before the
25.003 s public deadline. This proves model text availability and a live
search-tool boundary, but it does not prove authoritative web-answer success.
The application therefore keeps authoritative web status fail-closed and uses
the supplemental tier only for eligible non-operational explanation.

## 2026-08-14 hybrid multi-brain Cascade hardening

The first incorrect layers from the latest real-voice run were confirmed in
the local implementation before this pass:

- `CuratedProtocolSession.plan()` over-fenced pending confirmation and then
  allowed an immediate affirmative to fall into ordinary intent routing. The
  pending gate is now evaluated before generic scope, is compatible only with
  the next Turn and a non-older generation, and reuses the existing atomic
  completion/report/transition path.
- `classify_curated_control_intent()` did not distinguish next-step action,
  next-step information, and completion-criteria information. It also lacked
  scientific scope for rpm/incubation and a deterministic operational-deviation
  class. These are now separate server-policy actions.
- The old related-entity fields retained names but not an owned semantic focus
  and comparison/dimension context. `ProtocolDiscourseContext` now retains only
  bounded verified entities, topic, language, Turn/generation/revision, and is
  cleared or superseded on incompatible state changes.
- Anomaly classification was phrase-sensitive. Assertion variants and an
  explicit non-assertion guard now feed the existing server report gate; the
  classifier still cannot persist an event.
- Report acknowledgment was composed after next-step prose. It is now a
  mandatory server fact created only after existing report persistence succeeds
  and precedes the committed next-step introduction.
- Step 7 (and Step 9) has an observed repeat-until endpoint but no supported
  server completion signal. Step 20 retains unresolved source ambiguity. The
  implementation explains these constraints and does not invent a gate.
- Optional research had a provider deadline but no separate visible enrichment
  budget. A short Turn-owned budget now changes the UI to truthful bounded
  background status; the operation still has one hard terminal result.
- `renderCuratedProtocolState()` duplicated full visuals in Turns, and report
  content was permanently expanded. The Visual pane now owns the image, the
  Turn keeps a compact outcome, and report details expand on demand.
- No session greeting or actual three-role model boundary existed. A deterministic
  once-per-logical-session greeting and the typed orchestration below now exist.

### Authority and conditional activation

The course-explicit principle is that prompts request behavior while server
state, tool schemas, output gates, persistence, and tests enforce it. The names
**Answer Brain**, **Source Brain**, and **Visual Brain**, the activation matrix,
immutable snapshot, and timing values are project-specific engineering choices
for this Cascade application—not instructor mandates.

`multi_brain.py` starts only roles required by a semantic Turn. Clear stop,
navigation, completion, and report commands never construct a brain client.
Independent enabled roles are scheduled concurrently:

- Answer returns concise speech and richer display prose using only supplied
  evidence IDs. Unknown evidence, new operational numbers, completion claims,
  or persistence claims are rejected.
- Source returns a bounded entity/dimension/scope/query proposal. The server
  reconstructs the contextual query and alone executes/adjudicates retrieval.
- Visual returns only whether a visual helps, one supplied entity, and one
  requested visual class. The server visual gate owns provenance and execution.

Every call is bound to configuration, logical session, Turn, generation,
workflow revision, source hash, and step identity. Source and Visual cannot
speak; Answer cannot write state. The server is the only primary-answer/TTS
owner. The Answer role has a 1.25-second primary budget by default; if it is
still running, a deterministic admitted local answer is spoken first and safe
late prose can patch the same Turn as written-only enrichment. Each role retains
its hard timeout and is cancelled/fenced when ownership changes.

```bash
VOICE_WORKFLOW_AGENT_MULTI_BRAIN_ENABLED=false
VOICE_WORKFLOW_AGENT_MULTI_BRAIN_MODEL=grok-4.6
VOICE_WORKFLOW_AGENT_ANSWER_BRAIN_PRIMARY_BUDGET_SECONDS=1.25
VOICE_WORKFLOW_AGENT_ANSWER_BRAIN_TIMEOUT_SECONDS=8
VOICE_WORKFLOW_AGENT_PLANNER_BRAIN_TIMEOUT_SECONDS=6
EXTERNAL_REFERENCE_ENRICHMENT_BUDGET_SECONDS=4
```

Offline tests prove three-way overlap with a barrier, conditional activation,
strict output rejection, a non-cancelling primary budget, cancellation, and
zero external calls. Live model quality and Mac/Whale audio remain separate.

The bounded 2026-08-14 live schema check started exactly three text-only
OpenAI-compatible chat-completions requests (one per role; no tools, image,
audio, or workflow operation). The client produced no public terminal before
the harness was externally stopped at 615.3 seconds. This did **not** verify a
live role output. It exposed that `asyncio.wait_for()` could wait indefinitely
for transport cancellation cleanup. The orchestration now passes an explicit
per-request SDK timeout and uses a non-waiting public deadline that cancels and
collects late transport cleanup. A cancellation-resistant offline regression
proves the public terminal returns within its configured budget. No further
live call was made because the authorized three-request ceiling was exhausted;
live Provider cleanup remains a manual/operator recheck.
