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
one SQLite-backed draft per accepted procedure session. Start, committed step,
explicit anomaly, block, stop, and finalization events use stable idempotency
keys. Stop finalizes the draft as stopped/incomplete; it never means successful
procedure completion. Steps 7, 9, and 20 remain blockers. Markdown and JSON are
read-only same-origin exports. Runtime databases and report instances are ignored
and must never be committed.

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

At Steps 7, 9, and 20, repeat the compound-next phrase and verify a blocked
response, no completion assertion, and no index change. A clearer paraphrase is a
test input, never permission to resolve those execution controls.

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

Optional authoritative search is disabled by default. A separately authorized
manual run may export only these non-secret controls before invoking the launcher:

```bash
export VOICE_WORKFLOW_AGENT_EXTERNAL_REFERENCES_ENABLED=true
export EXTERNAL_REFERENCE_DOMAIN_PROFILE='candidate_a'
export VOICE_WORKFLOW_AGENT_EXTERNAL_REFERENCE_MODEL='grok-4.5'
export VOICE_WORKFLOW_AGENT_EXTERNAL_REFERENCE_TIMEOUT_SECONDS=20
```

The domain list above is an operator-visible example, not a blanket authority
decision. Confirm the institution/manufacturer domains appropriate to the actual
question. Live use also requires the already configured xAI credential through
the normal secret loader; never print it. One Turn makes at most one domain-filtered
Responses web-search request and revalidates every returned HTTPS citation.

Optional image generation is also disabled by default. For the single explicit
manual visual request, the non-secret controls are:

```bash
export VOICE_WORKFLOW_AGENT_GENERATED_VISUALS_ENABLED=true
export VOICE_WORKFLOW_AGENT_GENERATED_VISUAL_MODEL='grok-imagine-image-quality'
export VOICE_WORKFLOW_AGENT_GENERATED_VISUAL_TIMEOUT_SECONDS=60
```

Do not enable either feature merely to run offline tests. Automated tests use
fakes and make zero paid calls.

Optional web-image lookup is also explicit and uses the same authority profile:

```bash
export WEB_VISUAL_SEARCH_ENABLED=true
```

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
| `현재 실험 기록을 보여줘` | current report state/export; no workflow mutation |
| `예상과 다르게 색이 남아 있어` | one explicit anomaly event; no fabricated observation approval |

For Steps 7, 9, and 20, completion language must still return the existing
fail-closed execution-control reason. Neither an explanation, source crop,
external result, nor generated image can approve those boundaries.

## Glove-first browser checks

Use headphones, one Whale tab, one WebSocket, Cascade mode, Korean Manual mode,
and the exact Candidate A development protocol. At both 100% and 125% zoom:

1. Confirm the current-step pane and newest Turn remain visible together.
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
