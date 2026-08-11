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
