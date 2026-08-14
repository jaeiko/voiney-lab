# Candidate A real-voice Acceptance architecture

This document records the implementation boundary adopted from the authoritative
local worktree. Candidate A remains a development-only, `analysis_required`
curated fixture. Nothing in this design approves it, persists procedure state, or
changes the fail-closed execution controls at Steps 7, 9, and 20.

## Routing and state ownership

Cascade routing is ordered as follows: explicit stop; start/current/repeat;
audio recovery; completion-only/completion-and-next; other deterministic workflow
controls; anomaly/report/visual requests; current and adjacent protocol entities;
approved laboratory references; explicitly enabled authoritative web references;
and finally transcript clarification or a bounded off-topic response.
The server, never an LLM or retriever, validates and applies a transition. A
compound completion report is represented separately from its single requested
`next` transition so one utterance cannot advance twice. Ambiguous completion
language asks for confirmation without changing state. Off-topic speech is a
read-only route result and preserves protocol, step, language, and session.

Reviewed semantic normalization removes harmless punctuation, fillers, duplicate
speech tokens, and spacing variation before deterministic matching. It does not
perform fuzzy protocol-name or state inference. Natural repeat paraphrases remain
spoken-guidance replay only. A Step 3 reference to Solution A, A solution, AMBIC,
or an unambiguous "that solution" resolves to the exact adjacent Step 2 verified
fact; ambiguous entities ask for clarification rather than entering retrieval.
Named-step elaboration is a read-only Tier 0 view and never changes the current
step.

## Knowledge sources and ingestion

The existing explicit safety-document manifest, schema-validated SQLite catalog,
and approved/active/version/facility filters remain the policy authority. A shared
retriever projection adds stable chunk identities, ranked evidence, citation
metadata, deduplication, and read-only filters without creating a second approval
store. The deterministic SQLite backend is always available for offline tests.
The installed `moss` SDK is the `usemoss/moss` retrieval runtime; it may rerank
only candidates already admitted by the SQLite authority. Its index is opt-in,
cloud-backed at creation/load time, and never receives documents unless the
existing explicit sync command and configuration are used.

An optional xAI Responses web-search adapter is disabled by default. When enabled,
it requires a laboratory-related query and one to five configured authoritative
domains. HTTPS URL, domain, citation, and relevance checks happen before content is
accepted. Retrieved text is untrusted data: it cannot supply instructions to the
agent, request tools, expose secrets, or mutate workflow state. Internal and web
answers retain origin, source identity, exact excerpts, citations, backend, scores,
limitations, and the owning session/turn/generation. Korean primary text is spoken;
source excerpts and citations remain display-only.

Runtime retrieval hard-rejects records marked `demo`, `test_only`, fictional,
synthetic, or non-operational. A non-empty result set is not success: at least one
admitted citation must support an answer claim. The current VM catalog has two
approved demo documents and three sections, but no admissible Candidate A
guidance. An internal miss therefore continues to the web tier only when that
tier is explicitly enabled.

The model-facing Tool registry remains nine unique schemas. The broad approved
laboratory reference search, authoritative web search, and generated-visual job
are internal server operations rather than additional model Tools. Nine is an
intentional exception to the preferred eight because the existing approved
ProcedureStore workflow has distinct read, transition, observation, timer, and
audit capabilities, while safety lookup, confirmed report creation, and report
status have separate authorization and side-effect contracts.

## Generated instructional illustrations

Visual selection is a verified source crop by default and, only after an explicit
visual request, a validated generated-image cache or a new generated image.
There is no automatic explanatory schematic. When neither source nor generated
image is available, the UI keeps the grounded text and shows a compact
no-original-visual status. Generation is feature-flagged and uses
the xAI `/v1/images/generations` contract with the configured model (default
`grok-imagine-image-quality`) and base64 output. The prompt is built only from a
validated server-authored specification of current-step facts; raw user text and
web content are excluded. MIME, magic bytes, dimensions, size, document hash, and
cache key are validated. Assets are served by an opaque same-origin ID and are
labelled as AI-generated, non-source presentation material.

The accepted text/audio path never waits for image generation. A background job is
started or joined after the primary response boundary and patches only the owning
configuration, turn, generation, protocol, step, and source hash. Cancellation,
reset, disconnect, or identity mismatch discards the result. Image success or
failure cannot authorize completion, navigation, or a readiness change.

Real-image lookup is a separate, disabled-by-default xAI web-search operation.
It uses image search/understanding only after an explicit request for a real
photo/example. The server validates an HTTPS allowlisted source page. Because
the provider response does not establish copying rights for arbitrary remote
bytes, the implemented safe result is an attributed source-page card; it does
not hotlink, download, proxy, or relabel a thumbnail. Generated illustration is
the fallback only for a separate instructional-visual request.

## Experiment reports

When `VOICE_WORKFLOW_AGENT_EXPERIMENT_REPORTS_ENABLED=true`, Candidate A opens
one SQLite-backed draft per accepted procedure session. Start, presented step,
committed completion/navigation, explicit anomaly, block, consulted source,
system anomaly, stop, and finalization events use stable idempotency keys.
Accepted state changes are persisted before TTS; a completed event names the
pre-transition step and carries `completion_source=user_command`. Stop finalizes
the draft as stopped; it never means successful procedure completion. Steps 7,
9, and 20 remain blocked on a bare completion claim; an immediate explicit
user report of their source-defined visible endpoint is recorded before the
single validated transition. JSON, Markdown, and UTF-8 CSV are read-only
same-origin exports. Runtime databases and report instances are ignored and
must never be committed.

The older `create_safety_report` handoff Tool remains a distinct explicit safety
workflow for its existing callers. Experiment-report persistence, exports,
retrieval, web/image providers, replay, and cache are internal services and do
not expand the nine model-facing function schemas.

## Native interruption boundary

Native response/item identity now owns its received text and audio. A confirmed
barge-in preserves all text already received on the interrupted assistant Turn,
labels playback as interrupted, reports heard audio duration to the provider-
equivalent truncate control, clears only matching audio, and rejects late deltas.
The next user card is created only after meaningful committed input and the next
assistant card only after a new response begins. The browser reuses one audio
context, removes the fixed post-drain 10 ms scheduling delay, and reports provider
arrival gaps separately from client underruns. This is offline event-sequence
evidence; real Mac/Whale audio continuity still requires manual Acceptance.

## Conversation viewport and latency

Turns remain one canonical record. Transcript, bilingual answer, citations,
status, filler, route, server operation, real Tool calls, timing, and visual updates
patch that record idempotently. The browser renders oldest-to-newest in a bounded,
responsive chat viewport. It follows new content only while the reader is near the
bottom; otherwise it preserves the scroll anchor and offers a new-update control.
Late visuals reserve space and never move keyboard focus.

Measured server boundaries remain separate: STT, answer ready, first TTS request,
first playable audio, playback completion, retrieval, visual requested, and visual
ready. Failures are fail-closed and visible in the originating Turn. No timing is
inferred from animation, elapsed UI time, or model reasoning.

The glove-first document order is title/guide, protocol/PDF setup, then the active
procedure and compact visual beside the bounded chat viewport, followed by voice
controls and the experiment-report panel. The removed
standalone processing card is not a loss of state: each Turn already consumes the
server-authored monotonic lifecycle. A presentation-only normalizer repairs only
known extraction glyphs (`(`, `)`, `-`, `:`) and renders `mm3` as `mm³`; the
fixture, evidence excerpts, hashes, citations, and speech payload remain
unchanged.

## Real-audio validation plan

Automated tests use text, fake audio boundaries, and mocked Provider clients; they
do not prove microphone recognition. Run the following matrix with one fresh
Cascade/Korean/Candidate A browser session and headphones. Speak once per row,
wait for playback to finish, and never retry inside a measured run.

| Korean phrase | Expected route | Expected operation | State requirement |
| --- | --- | --- | --- |
| `프로토콜을 시작해 줘` | `curated_protocol` | start | active at Step 1 |
| `현재 단계 알려줘` | `curated_protocol` | current | unchanged |
| `다시 한번 설명해 줘` | `curated_protocol` | repeat | unchanged |
| `현재 현재 단계를 완료했어 다음 단계로 안내해 줘` | `curated_protocol` | compound next | exactly one advance |
| `이 단계 끝났어. 다음으로 넘어가요.` | `curated_protocol` | compound next | exactly one advance |
| `3단계를 좀 더 자세히 설명해 줘` | `curated_protocol` | current-step elaboration | unchanged; Tier 0 only |
| `그 용액은 어떻게 준비해?` | `curated_protocol` | verified fact or clarification | unchanged; exact fact ID only |
| `AMBIC은 어떻게 준비해?` | `curated_protocol` | verified fact or clarification | unchanged; exact fact ID only |
| `2단계 할 때 주의사항 같은 거 있어?` | related reference | approved retrieval or fail closed | unchanged |
| `혹시 융프라우 다녀오셨나요?` | off topic | scope reminder | active step preserved |
| `프로토콜 종료해 줘` | `curated_protocol` | stop | inactive, never completed |
| `지금은 총 몇 단계로 이루어져 있어?` | `curated_protocol` | protocol structure | 25 total; no retrieval |
| `현재 몇 번째 단계야?` | `curated_protocol` | protocol structure | current/25; unchanged |
| `몇 단계 남았어?` | `curated_protocol` | protocol structure | remaining after current; unchanged |
| `전체 흐름을 요약해 줘` | `curated_protocol` | protocol structure | five ordered source sections |
| `시작 전에 무엇을 준비해야 해?` | `curated_protocol` | protocol structure | verified prerequisites/materials/equipment |
| `전체 안전수칙을 알려줘` | `curated_protocol` | protocol structure | explicit warnings plus precise limitation |
| `12단계 설명해 줘` | `curated_protocol` | exact step lookup | Step 12 displayed; current step unchanged |

At Steps 7, 9, and 20, repeat the compound-next phrase. Verify that a bare claim
does not complete the step and that the agent asks for the exact visible source
endpoint. Reply once with both a positive and negative controlled observation:

- Step 7 positive: `젤이 완전히 탈색되어 투명해요`; negative:
  `아직 색이 남아 있어요`.
- Steps 9 and 20 positive: `젤이 흰색으로 변했고 탈수됐어요`; negative:
  `아직 투명해요`.

Verify the positive report is persisted before a single transition and that the
negative report keeps the step current. A model, source image, or generated
visual must never supply this observation. Also allow the pending question to
expire and verify a late reply cannot advance a later Turn.

Before the session, verify the browser sends `client.audio_ready` only after its
playback context is running. The one greeting must name the selected protocol,
be audible and interruptible, appear once without duplicated text, and not replay
after reconnect/resume. For an STT-correction run, operators may explicitly
enable the bounded diagnostic mode from `.env.example`; confirm audio/JSON pairs
stay under the ignored `data/runtime` directory and delete them after review.

For every turn retain a sanitized record with: audio asset ID (not raw audio),
normalized transcript, detected language, route, operation, step before/after,
fact ID or retrieval origin, tool events that actually ran, terminal state,
STT/answer-ready/first-audio/playback/visual timings, and pass/fail reason. Also
capture request counts, retries, redirects, database fingerprints, repository
fingerprints, and the fixture/source/schema identities. Never retain credentials,
headers, prompts, complete Provider bodies, raw PDF text, or evidence excerpts in
telemetry.

Rerunnable offline checks are:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 \
  .venv/bin/python -B -m unittest \
  tests.test_curated_protocol_cascade \
  tests.test_approved_references \
  tests.test_generated_visuals \
  tests.test_frontend -v

.venv/bin/python -B scripts/audit_approved_catalog.py \
  --db /absolute/path/to/catalog.sqlite \
  --scope reference_only \
  --query "2단계 acetonitrile 주의사항"
```

## Mac and Whale manual checklist

1. Use one Whale tab, one WebSocket connection, Cascade mode, Korean, and the
   exact Candidate A development protocol ID. Confirm `session.ready` before
   enabling the microphone.
2. Use a headset. Do not refresh, reconnect, open a second tab, or speak during
   playback/cooldown. Stop immediately on duplicate endpoints or responses.
3. Verify title/setup/PDF identity remains above the active-step and bounded chat
   workspace. Produce at least 15 Turns and confirm the page does
   not grow with the conversation.
4. Scroll upward, create a late Tool/visual update, and confirm the viewport keeps
   its reading position and offers the new-update control. Confirm all bilingual
   answer, source, citation, route, operation, Tool, filler, timing, status, and
   visual details remain accessible.
5. Confirm private-use extraction glyphs never appear in visible instructions;
   verify parentheses, hyphens, time colons, `mm³`, `µL`, and `37°C` render
   correctly. The underlying fixture and evidence must remain byte-identical.
6. At a step with a verified source crop, explicitly request a visual and confirm
   no generated replacement runs. At a step without one, first confirm routine
   navigation shows no automatic schematic. Then explicitly request a visual and
   confirm text and TTS arrive before any asynchronous generated illustration,
   the later patch stays on the same Turn, and the image is labelled AI-generated
   and non-source. A cache hit must not appear as a new Provider Tool call.
7. Confirm an approved-reference question either cites an actually approved active
   document or fails closed. The catalog currently configured on this VM contains
   fictional demo records only and is not Candidate A operational guidance.
8. Record the outcome table and preservation fingerprints. Passing this checklist
   is development Acceptance evidence only; it is not final protocol approval,
   automated-ingestion acceptance, production-safety validation, or Native parity.

## Exact Candidate A launch and optional live features

Run the existing launcher from the VM. It verifies the Candidate A PDF identity,
loads the exact curated fixture/provenance, and materializes only the already
reviewed development catalog entry before starting one Uvicorn worker:

```bash
cd /home/student/voice-ai-course/voice-workflow-agent
./scripts/run_candidate_a.sh
```

The launcher is deliberately the owner of the Candidate A paths. Do not duplicate
those paths in `.env`, and do not edit `.env` for Acceptance. The normal page is:

```text
http://localhost:8000/
```

When the browser runs on the Mac and the application runs in the VM, open one
terminal on the Mac and replace only the operator-owned VM host value:

```bash
ssh -N -L 8000:127.0.0.1:8000 student@<VM_HOST>
```

Global authoritative search remains disabled by default. The dedicated Candidate A
launcher now explicitly enables the reviewed `candidate_a` profile, web-image
source lookup, and request-only generated visuals. Its non-secret preflight prints
the capability state and domain count and fails before server startup on conflicting
aliases or invalid settings. It never prints the credential. Do not add extra
exports for the normal Candidate A run.

Equivalent canonical controls for a separate development launcher are:

```bash
export EXTERNAL_REFERENCES_ENABLED=true
export EXTERNAL_REFERENCE_DOMAIN_PROFILE='candidate_a'
export EXTERNAL_REFERENCE_MODEL='grok-4.6'
export EXTERNAL_REFERENCE_TIMEOUT_SECONDS=20
export EXTERNAL_REFERENCE_CONNECT_TIMEOUT_SECONDS=3
export EXTERNAL_REFERENCE_READ_TIMEOUT_SECONDS=15
export EXTERNAL_REFERENCE_CACHE_TTL_SECONDS=900
export EXTERNAL_REFERENCE_MAX_CITATIONS=5
export SUPPLEMENTAL_MODEL_KNOWLEDGE_ENABLED=true
export SUPPLEMENTAL_MODEL_KNOWLEDGE_MODEL='grok-4.6'
export SUPPLEMENTAL_MODEL_KNOWLEDGE_TIMEOUT_SECONDS=8
export WEB_VISUAL_SEARCH_ENABLED=true
export VOICE_WORKFLOW_AGENT_GENERATED_VISUALS_ENABLED=true
export CASCADE_BARGE_IN_PREFIX_MS=800
```

The domain list above is an operator-visible example, not a blanket authority
decision. Confirm the institution/manufacturer domains appropriate to the actual
question. Live use also requires the already configured xAI credential through
the normal secret loader; never print it. One Turn makes at most one domain-filtered
Responses web-search request and revalidates every returned HTTPS citation.

Image generation and web-image lookup remain explicit-request-only even though
the dedicated launcher makes them available. No visual lookup or generation runs
on routine step transitions. For another launcher the model/timeout controls are:

```bash
export VOICE_WORKFLOW_AGENT_GENERATED_VISUALS_ENABLED=true
export VOICE_WORKFLOW_AGENT_GENERATED_VISUAL_MODEL='grok-imagine-image-quality'
export VOICE_WORKFLOW_AGENT_GENERATED_VISUAL_TIMEOUT_SECONDS=60
```

Do not enable either feature merely to run offline tests. Automated tests use
fakes and make zero paid calls.

The sanitized diagnostic is offline unless `--live` is present. Each live
invocation makes exactly one bounded request and reports only timings, event/tool
counts, terminal state, citation count/domains, and a safe request identifier:

```bash
source .venv/bin/activate
python -B scripts/diagnose_candidate_a_research.py --live --query-profile ambic
python -B scripts/diagnose_candidate_a_research.py --live --query-profile hplc-water
python -B scripts/diagnose_candidate_a_research.py --live --query-profile solution-a-role
python -B scripts/diagnose_candidate_a_research.py --live --query-profile step-safety
```

Run these only with the reviewed profile and existing credential. Stop after the
four calls; do not paste keys, headers, response bodies, or unrestricted page
text into Acceptance records.

The bounded 2026-08-12 implementation diagnostic used the full six-text-request
task budget. One pre-fix stream was manually cancelled when SDK cleanup outlived
the intended deadline. After cleanup was independently capped, AMBIC, HPLC-water,
Solution-A-role, and step-safety profiles each returned `timeout_total` at about
20.06–20.08 seconds with zero Provider events, zero successful tools, and zero
citations; a final AMBIC comparison at the 30-second hard maximum returned the
same state at 30.053 seconds. The step-safety request was also repeated outside
the restricted execution sandbox and behaved identically, so sandbox networking
was not the differentiator. No live external answer or web image was admitted,
and no live image-generation call was made. Treat the feature as implemented and
offline-verified but not live-provider-verified; do not spend further calls until
the Provider produces a first event within the hard budget.

The 2026-08-13 isolation matrix supersedes only the “no first event” diagnosis,
not the live-success status. A text-only `grok-4.6` Responses stream completed
in 3.218 seconds (first event 1.344 seconds; first text 2.329 seconds). A raw
PubChem-domain web-search stream then produced 15 tool-related events but no
completed response, answer text, or admitted citation before the 25.003-second
public deadline. Two of the three authorized requests were used; the matrix
stopped early because the lower raw-web boundary failed. Authoritative external
answer success remains **not live-provider-verified**. The optional general
model tier may be checked separately only for non-operational background and
must retain its non-authoritative label.

The Candidate A launcher enables the experiment-report service at an ignored
runtime path. For another launcher, use only an ignored absolute path:

```bash
export VOICE_WORKFLOW_AGENT_EXPERIMENT_REPORTS_ENABLED=true
export VOICE_WORKFLOW_AGENT_EXPERIMENT_REPORT_DB=/absolute/ignored/runtime/experiment_reports.sqlite
```

## Real-failure regression matrix

In one fresh Cascade/Korean session, speak each phrase once and wait for the
complete answer. Record raw speech separately from the normalized STT transcript.

| Phrase | Expected result |
| --- | --- |
| `현재 단계를 완료했어요.` | one atomic completion/advance; no retrieval |
| `이 단계 완료했어.` | one atomic completion/advance; no retrieval |
| `현재 단계를 완료했어, 다음 단계로 안내해줘.` | one atomic transition, never two |
| `다음 단계로 안내해 줘.` / `Guide me to the next step.` | ask whether the current step is complete; no transition and no report event |
| `네.` immediately after that question | one validated transition and one persisted completion event |
| `아니, 아직 안 끝났어.` immediately after that question | keep the step and clear the pending confirmation |
| `거의 끝난 것 같아.` | short confirmation request; no mutation |
| `이 단계를 좀 더 자세히 설명해 줘.` | concise Korean speech plus richer admitted evidence on screen |
| `젤 밴드가 완전히 탈색된다는 게 무슨 의미야?` | transparent endpoint, usual two cycles, Step 7 remains blocked |
| `그 용액은 어떻게 준비해?` | dominant recent entity or explicit A/B clarification |
| `여기서 진짜 안전 수칙 있어?` | protocol → approved catalog → optional authoritative web; no mutation |
| `출처 보여줘` | existing exact evidence displayed; no state operation |
| `웹에서 더 찾아봐` | one external follow-up only when a prior related question and feature exist |
| `방금 검색 취소해` | read-only lookup cancellation acknowledgment; protocol remains active |
| `소리가 안 나요.` / `There's no sound.` | last replayable answer once plus visible Replay control |
| `わんねーちょ` in Korean Manual mode | transcript retry, unless an explicit stop was clearly recognized |
| `혹시 융프라우 다녀오셨나요?` | short scope reminder; current step preserved |
| `AMBIC가 뭐야?` | related entity; direct definition then current-step relationship; unchanged |
| `HPLC water가 일반 물하고 뭐가 달라?` | related entity; protocol first, then admitted references; unchanged |
| `AMBIC에서 bicarbonate는 왜 중요한 거야?` | related role question; protocol/evidence ladder, never generic off-topic |
| `그 물을 왜 사용하는 거야?` after HPLC-water answer | resolve the one bounded recent entity; unchanged |
| `젤 플러그가 왜 완전히 탈색되어야 해?` | related expected-result question; Step 7 remains blocked |
| `염색된 단백질 밴드에서 케라틴 오염이 왜 문제가 돼?` | related safety question; no anomaly/report event and no model-only safety answer |
| `HPLC water 대신 일반 증류수를 써도 돼?` | preserve the protocol requirement; never approve substitution from supplemental knowledge |
| `다음 여행지는 어디가 좋아?` | bounded off-topic response; zero research and mutation |
| `여기서 HPLC water하고 ANBI-C가 뭐야?` | ordered HPLC-water and AMBIC answers plus an auditable correction note; unchanged |
| `염색된 단백질 밴드가 어떤 걸 의미해? 혹시 그림을 보여줄 수 있어?` | direct definition first; the same Turn receives a source visual, source card, or honest unavailable result |
| `Jel Tug에 관해서 이미지를 보여줄 수 있어.` | contextually resolves gel plug and enters the read-only entity-visual route |
| `다음 단계로 안내해 줘. 현재 단계 완료했어.` | one atomic transition regardless of clause order; never two |
| `장기를 완료했어.` | one short current-step completion confirmation; no mutation before confirmation |
| `현재 실험 기록을 보여줘` | current report state/export; no workflow mutation |
| `예상과 다르게 색이 남아 있어` | one explicit anomaly event; no fabricated observation approval |
| `지금은 총 몇 단계로 이루어져 있어?` at Step 6 | `25`, current `6/25`, remaining `19`; zero retrieval/Provider calls |
| `프로토콜을 종료할게` | one stopped-by-user transition and one report finalization |
| `종료 조건이 뭐야?` | a read-only question, never a stop command |
| `Cough.` / `[throat clearing]` / `keyboard` | `speech.rejected`; no normal Turn, TTS, research, mutation, or report event |

For Steps 7, 9, and 20, bare completion language must ask for the exact visible
endpoint and perform zero mutation. Only the user's immediate compatible report
can satisfy that server gate. Neither an explanation, source crop, external
result, generated image, nor model output can approve those boundaries.

For a research Turn, verify one terminal status is visible even on failure.
While it is running, start a newer meaningful Turn: the older Turn must change
to “새 요청으로 이전 근거 확인 종료,” and a later Provider success must not
revive or replace it. If the optional supplemental tier runs, its Turn must say
“일반 모델 설명 · 확인된 권위 근거 없음,” expose no citation, and keep the
workflow/report state unchanged.

## Glove-first browser checks

Use headphones, one Whale tab, one WebSocket, Cascade mode, Korean Manual mode,
and the exact Candidate A development protocol. At both 100% and 125% zoom:

1. Confirm Procedure and Original/Related Visual are the primary side-by-side
   panels and the bounded Turn history appears below them. At narrow/mobile
   width the two primary panels must stack without horizontal overflow.
2. Produce at least 15 Turns; only the bounded chat viewport should scroll.
3. Scroll upward and allow a late search/visual patch; focus and scroll anchor
   must remain stable and the new-update affordance must appear.
4. Confirm every historical Turn still exposes route, server operation, real
   service calls, status, bilingual evidence, citations, limitations, timing,
   filler, replay, and visual state.
5. Confirm routine Steps without original images show no SVG/canvas/arrow or
   blank visual space. Historical generated images remain bounded thumbnails.
6. Confirm derived display and TTS show `1 mm³`, `(AMBIC)`, and `00:15:00`
   without boxed private-use glyphs; the exact raw evidence remains unchanged.
7. Record STT, detected language/quality metadata, route, state before/after,
   operation count, source tier, spoken/written answer, citations, and the
   existing STT/answer/first-audio/playback/retrieval/visual timing fields.

## Focused Cascade research and barge-in checklist

1. Confirm the setup chips say external search and web-image lookup are available,
   show `candidate_a`, and expose no key or full query.
2. Speak `A M B I C가 뭐야?`, `A M P I C가 뭐야?`, `에이엠빅이 뭐야?`,
   `PLC water is what?`, and `HPLC 워터가 뭐야?`. Confirm a unique bounded
   correction is shown, the raw transcript remains visible, and the state does not
   change.
3. After an internal `no_admissible_evidence`, confirm one real
   `search_authoritative_web` Tool call/result appears with two to five validated
   citations. A partial protocol composition must not suppress a requested
   definition, role, difference, or safety search.
4. While an answer is speaking, interrupt once with `여기서 지켜야 할 안전 수칙은
   뭐야?`, then with `HPLC water를 왜 쓰는 거야?`. Confirm playback ducks at
   the candidate boundary, the committed transcript retains its first meaningful
   token, rejected cough/tap candidates resume the matching playback, and no
   provisional Turn card is created.
5. Repeat once using speakers. If the setup chip says echo cancellation is off or
   unavailable, record the headphone recommendation rather than treating AEC as
   active. This speaker run is diagnostic, not a production-safety claim.

## Native Mac/Whale interruption checklist

1. Select Native comparison mode and request an answer long enough to interrupt.
2. Barge in after 1–2 seconds. Confirm audible output stops, received answer text
   stays on the original assistant Turn, and its label says user speech
   interrupted playback rather than generic stopped.
3. Confirm the committed new transcript and new response each receive their own
   cards; false VAD starts and zero-content cancellations create none.
4. Repeat 10 uninterrupted responses. Inspect the collapsed diagnostics for one
   reused AudioContext, zero client-underrun count, and provider/source gaps
   reported separately. DOM rendering must not be on the audio scheduling path.
5. Treat content and audio quality as separate pass/fail axes. This checklist does
   not make Native authoritative for Candidate A workflow execution.

## Grounded protocol, report, and transport evidence fields

1. At Step 6 ask every whole-protocol question above. Record the raw transcript,
   `intent_kind`, server operation, spoken answer, full display, source pages,
   current index before/after, internal/tool calls, and Provider request count.
   Total/current/remaining must be 25, 6/25, and 19 with no search call.
2. Complete one ordinary step while forcing a development-only TTS failure.
   Verify the report already contains exactly one `step_completed` event for the
   pre-transition step and the next step remains committed. Inspect the event's
   pre/post IDs and `completion_source`.
   In a separate isolated failure injection, make report persistence fail before
   TTS: the spoken/displayed answer must say that the completion could not be
   committed, the previous step must remain current, and neither “실험 기록에
   반영” nor a `step_completed` event may appear. A bare next-step confirmation
   question must create no report draft or event.
3. Stop naturally with `프로토콜을 종료할게`, then repeat it. Verify one
   `session_stopped`, one `report_finalized`, final status `stopped`, and working
   per-report JSON/Markdown/CSV downloads. Confirm the report remains available.
4. While a related-question supplement is running, start a newer valid Turn and
   then issue a stop command. Verify the earlier request is cancelled or its late
   result is ignored, the local answer remains visible, and no supplement attaches
   to the newer Turn.
5. Speak cough, throat-clear, sniff, keyboard/tap, chair/noise, silence, and music
   controls. Record VAD/STT metadata when available. Verify whole-event labels are
   rejected and `네`, `아니요`, `다음`, `중지`, `stop`, a step number, and
   `AMBIC` are not rejected merely for being short.
6. In Native comparison mode, record browser connection identity, hashed
   application-session reference, upstream epoch, model, safe conversation
   reference, last Provider event, uptime, close initiator/code/reason, and
   resumption result. Ten ordinary responses must not create a new epoch.
   Injecting one genuine transient close may create exactly one resumption.
7. The configurable Native model remains `grok-voice-latest` until the existing
   account passes a version-specific compatibility probe. Record this as not
   live-verified rather than changing the model during Acceptance.

Pause was intentionally not added because Candidate A has no authoritative pause
or timer-checkpoint operation to reuse. Resume continues through the validated
start/resume command. A client-only Pause control would violate server ownership.

## Final hybrid Cascade Mac/Whale checklist (2026-08-14)

Start with `scripts/run_candidate_a.sh`, one Whale tab, headphones, Cascade,
Korean Manual mode, and the exact Candidate A development fixture. Record raw
STT, resolved Turn language, route/hard gate, state/report counts before and
after, enabled brain roles, source scopes, speech/display text, first audio,
total time, and terminal source/visual status. These remain manual until
exercised with a real microphone and audible browser output:

1. A new usable session speaks one short Korean greeting. Interrupt it;
   reconnect/resume the same logical session and confirm it does not replay.
2. `다음 단계로 안내해 줘.` creates no mutation, then `네, 완료했어요.`
   persists once, speaks the verified record acknowledgment first, and
   transitions exactly once. Repeat with `다음 단계로 이동할게`, `옮겨`,
   `let's continue`, and `아니요, 아직 안 했어요.`. The progression forms
   may inherit completion meaning only while that one-Turn gate is owned.
3. `What is the next step?` gives an English Step N+1 preview with zero
   mutation; `What is the current step?` gives the English current step.
4. `완료 조건이 뭐야?` explains required/satisfied/missing or explicitly
   unspecified criteria without a raw procedure dump.
5. Ask `HPLC water가 뭐야?` then `그거 일반 물이랑 뭐가 다른데?`;
   ask `AMBIC가 뭐야?` then `그거는 왜 여기서 사용하는 거야?`.
6. Ask `800 rpm이 무슨 뜻이야?`; confirm scientific scope and zero mutation.
7. At Step 3 ask `37도 대신 35도로 해도 돼?`; confirm approved 37°C is
   restated, the deviation is not authorized, and state is unchanged.
8. In isolated report cases say `예상과 다르게 아직 색이 남아 있어.`,
   `물질 색깔이 조금 이상해.`, and `색깔이 변형됐어.`. Confirm consistent
   assertion handling and post-persistence acknowledgment. Then ask `색깔이
   변하는 건 무슨 의미야?` and confirm no anomaly write.
9. Start enrichment, then say `아니, 그건 됐고 현재 단계 다시 알려줘.`
   Confirm the local answer/TTS was prompt and the old result is superseded once
   and cannot revive.
10. At Steps 7, 9, and 20 attempt bare completion. Confirm the agent asks for
    the exact source endpoint and does not move. Reply with one controlled
    positive and one negative observation; only the immediate positive report
    may be persisted and transition exactly once.
11. Ask `HPLC water와 일반 물의 차이를 설명하고 관련 그림도 보여줘.`
    Confirm Answer+Source+Visual diagnostics, one text/TTS owner, Visual-panel
    image ownership, and zero state mutation.
12. At 100%, 125%, and mobile width verify Procedure and Visual are primary,
    Turn history scrolls below, the report is compact, late patches preserve
    focus/scroll, and no full visual/protocol dump is duplicated in a Turn.

Do not mark live xAI authoritative search, generated images, real microphone,
audible TTS, perceived latency, or Whale layout verified from offline fakes.

The 2026-08-14 three-role live schema check used the entire three-request text
budget and was stopped after 615.3 seconds without a typed terminal result.
The resulting transport-deadline repair is offline-verified, but the live role
contract is not. Before Mac Acceptance, rerun one explicitly approved bounded
role request and verify that the SDK timeout and public terminal both close it;
do not infer this from the cancellation-resistant fake.
