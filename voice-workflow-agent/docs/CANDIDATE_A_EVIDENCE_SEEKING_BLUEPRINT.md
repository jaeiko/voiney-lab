# Candidate A evidence-seeking hardening blueprint

This blueprint records the implementation boundaries adopted from the
2026-08-10 real-voice Acceptance failures. Candidate A remains a
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

## Reference adoption matrix

| Reference | Pattern | Decision | Reason / dependency |
|---|---|---|---|
| OpenAI voice-agent guidance | Chained STT → application logic → TTS | Adapt | Matches the existing deterministic Cascade authority; no new dependency. |
| OpenAI realtime evaluation guide | Preserve stage traces and turn real failures into regressions | Adapt | Extends the existing offline fixture/evaluation style; no new dependency. |
| Google ADK session state | Bounded structured short-term session state | Adapt | Implement locally in `CuratedProtocolSession`; no framework dependency. |
| xAI Responses web search | Domain-restricted read-only evidence lookup | Adapt existing adapter | Uses the installed OpenAI-compatible client; live use remains feature-gated. |
| xAI image generation | Base64, source-bound instructional image | Adapt existing adapter | No automatic generation; live use remains feature-gated. |
| `civiliangame/meet_AGI` | Separate audio transport, session state, recovery, and product UI concerns | Adapt selected patterns | Do not copy its agent framework or multi-agent orchestration. |
| Google Custom Search JSON API | New search provider | Reject | Closed to new customers and on a published transition path; no dependency added. |
| YouTube `search.list` | Video discovery metadata | Reject as evidence | Search metadata is not validated laboratory content or a source transcript. |

## Failure boundary

No LLM, retrieved document, webpage, image, replay button, or UI event can
advance, stop, reset, approve an observation, or resolve Steps 7, 9, or 20.
Provider-disabled paths must remain useful and truthful without pretending a
search or image call occurred.
