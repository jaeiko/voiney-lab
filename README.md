# Voice Workflow Agent

[![CI](https://github.com/jaeiko/voiney-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/jaeiko/voiney-lab/actions/workflows/ci.yml)

Canonical repository: [`jaeiko/voiney-lab`](https://github.com/jaeiko/voiney-lab).
This project originated inside the course repository
[`jaeiko/voice-ai-course`](https://github.com/jaeiko/voice-ai-course) (a fork
of `civiliangame/voice-ai-course`); that repository is preserved as historical
record but is no longer where active development happens.

Voice Workflow Agent is a voice-first laboratory protocol knowledge and
execution layer. Its product wedge is:

> reviewed protocol source → hands-free bench execution → auditable experiment
> record → integration

It combines immutable source ingestion, human-controlled protocol lifecycle,
deterministic workflow mutation, source-grounded voice guidance, and controlled
downstream write-back. It is a controlled-pilot system—not a validated
GLP/GMP/clinical system, a full ELN/LIMS, an autonomous scientist, or a safety
authority.

## Product contract

- The active voice path is Cascade: browser PCM → WebRTC VAD → STT → shared
  intent arbitration → deterministic workflow/tool boundary → TTS. STT and TTS
  each have a provider setting (`xai` by default; `google_cloud`,
  `elevenlabs`) — see [Speech providers](#speech-providers).
- Protocol learning, audit, history, combined “why + next,” visual requests,
  completion, pause/resume, and interruption stay on the production WebSocket
  path covered by integration tests.
- Model prose cannot advance a step, record an observation, start a timer,
  approve a protocol, resume blocked work, or write to an ELN.
- Read-only questions do not mutate workflow state. Combined explanation/next
  requests explain and preview, then wait for explicit completion.
- Every executable session is pinned to an exact protocol revision and source
  identity. Source changes create drafts; they never overwrite an approved
  revision or rebind a running experiment.
- Parsing, structural readiness, hazard review, human approval, and operational
  authorization are separate gates.
- Raw audio, unrestricted transcripts, prompts, model reasoning, and connector
  secrets are excluded from persistent pilot analytics.

## Architecture

```text
Local PDF / protocols.io / Drive / GitHub
  → source connector boundary
  → immutable source identity + tenant-scoped lineage revision
  → inbox + source/evidence review + diff
  → reviewer decision / explicit non-operational development activation
  → exact executable protocol revision

Browser AudioWorklet (16 kHz PCM)
  → FastAPI WebSocket + FrameBuffer + WebRTC VAD
  → one batch STT request per utterance (xAI POST /v1/stt by default)
  → language-consistency and transcript-admission gates
  → shared RequestArbitration
      ├─ emergency and deterministic state gates
      ├─ curated/executable protocol router
      ├─ approved reference retrieval
      └─ bounded general agent tool loop
  → sentence-segmented TTS (xAI by default)
  → canonical events and browser playback

Canonical workflow events
  → tenant-owned persistent ExperimentSession
  → append-only experiment timeline and report
  → JSON / Markdown / CSV / DOCX export
  → explicit confirmed eLabFTW write-back
  → tenant-scoped privacy-safe aggregates
```

The main runtime routing boundary is
`src/voiney_lab/runtime_routing.py`. An optional, disabled-by-default
semantic intent fallback (`semantic_intent.py`) sits behind it: when
deterministic routing returns a catch-all, it may *propose* one of the existing
bounded workflow actions, and server-owned policy in the same boundary decides
whether that proposal is used. It never mutates workflow state - see
[Semantic intent fallback](#semantic-intent-fallback). An LLM router that
takes over intent judgment behind deterministic front rules is wired behind a
setting that is off by default - see
[LLM router (off by default)](#llm-router-off-by-default).
Tenant/RBAC logic is in
`identity.py` and `workspace_store.py`. Protocol source adapters are in
`protocol_sources.py`; computational metadata is in `drylab_workflows.py`; the
ELN boundary is in `eln_connectors.py`.

The current component, authority, and persistence design is documented in
[`docs/CURRENT_ARCHITECTURE.md`](docs/CURRENT_ARCHITECTURE.md). The older
[`docs/ARCHITECTURE_MAP.md`](docs/ARCHITECTURE_MAP.md) is a labeled
pre-extension snapshot. Current capabilities and documentation authority are
indexed in [`docs/DOCUMENTATION_INDEX.md`](docs/DOCUMENTATION_INDEX.md).
Forward-only storage details are in
[`docs/MIGRATION_NOTES.md`](docs/MIGRATION_NOTES.md).

For operators and pilot participants, start with the
[`Pilot Deployment Guide`](docs/PILOT_DEPLOYMENT_GUIDE.md),
[`User Guide`](docs/USER_GUIDE.md), and
[`Troubleshooting Guide`](docs/TROUBLESHOOTING_GUIDE.md).

## Persistent experiment sessions

When workspace mode is enabled, every accepted voice configuration is bound to a
tenant-owned `ExperimentSession` pinned to the exact protocol revision. The
session begins in `ready`; the existing deterministic START intent moves it to
`in_progress`. Step completions update an append-only completed-step set and the
current-step recovery projection. Pause, resume, stop, block, and completion are
explicit lifecycle events.

A WebSocket reconnect may supply the server-issued `experiment_session_id` and
`experiment_session_version`. Recovery succeeds only for the original protocol
and revision and a fresh optimistic version. The server restores only contiguous
completed steps and the authoritative current step. It does not restore pending
confirmations, model output, conversation history, or active timers.

`GET /api/workspace/experiments` lists sessions visible to the current role;
`GET /api/workspace/experiments/{session_id}` returns the durable event history.
The dashboard transition endpoint permits explicit pause/resume/stop/block but
cannot claim completion—completion remains a protocol-authority action.

Each started session also has an append-only observation timeline. Researchers
can say “메모 추가해: …”, say “record observation” and answer the bounded
follow-up, or add a manual note from the bench workspace. Observation wording is
stored with its exact protocol step, author, category, capture source, and
timestamp as `observation_only`; it never becomes an instruction or approved
protocol fact. Source-defined positive/negative observations continue through
the existing completion gates.

Images and documents can be attached as opaque evidence. Uploads are streamed,
capped at 32 MiB, hashed, and stored by content identity. The system records
metadata with `not_interpreted` status and does not run OCR or image/document
interpretation. Internal storage references are not returned by JSON APIs or the
timeline UI. An authorized tenant member can download the original bytes through
a generated same-origin link; the server rechecks storage containment, regular-
file type, recorded size, and SHA-256 before returning it with `no-store`.

Timeline endpoints are:

- `GET /api/workspace/experiments/{session_id}/timeline`;
- `POST /api/workspace/experiments/{session_id}/observations`;
- `POST /api/workspace/experiments/{session_id}/evidence`;
- `GET /api/workspace/experiments/{session_id}/evidence/{evidence_id}`; and
- `POST /api/workspace/reviewer/experiments/{session_id}/actions`.

## Researcher screen wording

The researcher screen is written for a wet-lab bench (decision of 2026-10-05,
lane U). It only changes what is shown; every state it shows is the server's.

- **Identifiers stay folded.** Revision ids, hashes, record ids and principal
  ids are not in the body. Each sits in a closed "개발 상세 정보" block next to
  the readable name, so it can still be checked later. An experiment record
  reads "실험 기록 · {protocol title} · {start date and time}" (two records of
  one protocol started the same day are numbered "· 2번째"); the approval reads
  "개발용 초안(승인 전)" or "승인본 · {approval date} · {approver role}". The
  names are built in the browser from `experiment.report.state` (`started_at`,
  `protocol_id`) and the catalog entry (`title`, `approval`). The server also
  sends (lane R6, decision 5; the screen does not read them yet):
  `protocol_title`, `day_sequence` and `day_sequence_date` (the Nth record
  started that UTC day) on `experiment.report.state` and on each
  `/api/workspace/experiments` item, and `actor_display_name` on a catalog
  approval where the approver had one.
- **Evidence candidates** in a reviewer finding are one row each — checkbox,
  page, a one-line excerpt (full excerpt on hover) — in a list of fixed height
  that scrolls inside itself. Pressing the finding button with nothing ticked
  says so beside the button and sends nothing; the server refuses an uncited
  finding either way.
- **A refused finding** says why and what to do on one line beside the button.
  `protocol_approval_denied` is the catalog's refusal of the finding's content
  (for example a citation that does not resolve), not a permission check, and
  is shown that way; `authorization_denied` names the reviewer role. Since
  lane R6 (decision 5) the server answers a finding refused for its content
  with its own code and 400/422 instead: `finding_evidence_missing`,
  `finding_evidence_span_mismatch`, `ambiguity_not_found`,
  `analysis_revision_missing`, `finding_target_not_found`,
  `finding_value_mismatch`, `finding_not_recorded` (422) and
  `finding_unsupported`, `finding_value_invalid` (400). Only a real
  permission refusal is 403.
- **A turn card shows one status.** The end of a turn ("완료", "중단됨",
  "차단됨", "오류") is shown once; the red line under it is kept for the text of
  a real failure. When a later utterance is committed, an earlier card that
  never received its end state stops showing a progress label such as
  "재생 중…"; its developer details say the end state was not received.
  Since lane R6 the server sends that end state itself: a playback.ended held
  back by an open barge-in candidate ends the turn "complete" once the
  candidate is rejected, and a committed next turn sends the old turn's
  "cancelled" `turn.state` with `cascade.playback.clear`.
- **Pause.** While `protocol.fixture.state` says `workflow_status:"paused"`, the
  step card shows a large "다시 시작" button and the rail button reads
  "▶ 다시 시작". `experiment.ended` and `workflow.control.refused` disable both
  and say why.
- **Microphone.** Capture asks for `echoCancellation`, `noiseSuppression` and
  `autoGainControl`, and reads back what the browser applied. When echo
  cancellation is not on, the body says the speaker may be heard as the
  researcher's words and suggests headphones.
- **The microphone stays open (lane U2, 2026-10-06).** It is opened once, at
  `session.ready`, and kept open across turns, playback, pause and resume;
  only ending the session (or an error that ends it) closes it. Frames are
  sent as before — while the session is active and the socket is open. A
  track that ends, or a `devicechange` that takes the device in use with it,
  is checked after 0.8 s and the microphone is opened again once, into the
  same AudioWorklet. A failed attempt, or a second loss within 30 s, is not
  retried: the voice console shows the reason and a "마이크 다시 연결" button.
- **Choosing the microphone.** "마이크" under the protocol choice lists the
  browser's input devices by name. The choice is kept in this browser's
  storage (`voiney-lab.mic-device`, id and name; Safari changes ids, so the
  name is matched too). A remembered device that is not connected is marked
  "연결 안 됨" and the system default is used. Changing it during a session
  opens the new device once.
- **Remaining words (lane U2, decision 5).** The step card names the step
  once, "4단계 · 전체 25단계", and the rail "4단계 / 25"; the English
  "Step N" line is gone. Timers read minutes and seconds, "⏱ 14:55 남음".
  The "관리자 인계" row is shown, in red, only for a real block; with nothing
  handed over it is not shown. The readiness and development status
  ("확인 기록") is in the step card's "개발 상세 정보". A review choice reads
  "이 근거로 해결된 것으로 표시" or "기록만 하고 해결로 표시하지 않음", and a
  review group with nothing in it (an empty "실행 전 확인 조건") is not drawn.
- **Where reference words come from (decisions 5–7).** The reference panel is
  titled by its latest entry: "PDF 밖 설명 · AI 일반 지식" for an
  outside-PDF explanation, "AI 일반 지식" for other model knowledge (the
  same title as in the conversation card, lane U3 decision 5), "웹 참고 자료" only for an external answer with web citations,
  "추가 참고 자료" for the approved lab corpus. An outside-PDF explanation —
  `research.result` with `outside_pdf: true` (its `source_label`, "AI 일반
  지식", is shown), or a reply whose `reply.delta` carries the
  `display_document` "notice" section or the `outside_pdf_explanation`
  limitation — is drawn in a light dashed frame named "PDF 밖 설명 · AI 일반
  지식" in the conversation card and the panel. The mark is kept from
  `reply.delta` for the `reply.complete` render, which carries the text
  alone; a `reply.complete` that carries `display_document` itself is drawn
  from its own value. On a protocol turn it now carries the same
  `display_document` as its `reply.delta` (lane F, decision 3). With web references off
  (`research_capabilities.external_text` not `enabled`), a reference check
  that ends without an answer is not shown to the researcher; it is a line in
  the turn's developer details.
- **Server values on the screen (lane U3).** The screen reads the values lane
  R6 added for it and shows them, never changing what the server decided.
  - *Record names.* An experiment record is "실험 기록 · {protocol_title} ·
    {local date and time} · N번째", with N the server's `day_sequence` (shown
    from the second record of a day, or on the first once another record of
    that day is in the same list). The server counts by the UTC day
    (`day_sequence_date`) and the screen shows local time, so the server
    count is used only when the two days are the same; otherwise, and when
    the server sends no such values, the screen counts the records this
    browser has seen, as before. The experiment list
    (`/api/workspace/experiments`) uses the same title and count.
  - *Approver.* An approval reads "승인본 · {local date} · {actor_display_name}";
    without a recorded name, the role as before.
  - *Refused findings.* Each reason code from `/findings/*` (422/400) has one
    line saying what to do, e.g. `finding_evidence_missing` "원문 근거를 하나
    이상 고른 뒤 다시 눌러 주세요". Only a permission refusal (403
    `authorization_denied`) reads "검토자 계정으로 로그인한 뒤 다시 눌러
    주세요".
  - *Cut-off playback.* A `turn.state` `complete` or `cancelled` that the
    server sends for an earlier turn replaces the screen's own clearing of
    that card: the card shows the server's end and the "화면 정리" line in
    its developer details is removed.

## Protocol onboarding and lifecycle

The browser implements the explicit lifecycle:

```text
uploaded
  → analysis_pending
  → analyzing
  → analysis_ready
  → review_required
  → executable_draft OR blocked
  → approved
  → revoked
```

1. `POST /api/protocols?filename=...` streams a PDF to a bounded temporary file,
   validates its type/size/encryption state, extracts pages, calculates its exact
   SHA-256, and stores immutable bytes.
2. The browser calls `POST /api/protocols/{id}/analysis`. The API responds `202`
   and runs analysis in a background task; the browser polls
   `GET /api/protocols/{id}/analysis/status` until a terminal state.
3. `GET /api/protocols/{id}/review` exposes source identity, evidence, structure,
   warnings, missing values, readiness blockers, and lifecycle gates.
4. In `demo`, `reference_only`, or `test_only`, a user may explicitly activate a
   guidance-ready revision as a development-only draft. Operational scope never
   permits this shortcut.
5. Approval/revocation history is append-only. Revocation prevents new
   operational sessions but does not erase historical experiment provenance.

For a scanned/text-empty PDF, onboarding pauses before structured analysis. A
reviewer explicitly calls `POST /api/protocols/{id}/ocr`; the server invokes only
the trusted deployment-injected `ProtocolOcrProvider` and validates the exact
source SHA-256, complete ordered page set, bounded text, provider identity,
confidence, language, and warnings. The browser polls `GET
/api/protocols/{id}/ocr`, renders page text with `textContent`, and requires an
accept/reject decision at `POST /api/protocols/{id}/ocr/review`. Acceptance only
makes the reviewed text eligible for a separate structured-analysis request. It
does not start analysis, approve a revision, or make anything executable.

A readable PDF with only some pages marked `ocr_required` does not pause: its
readable pages are analysed as usual, and the same OCR route
(`GET`/`POST /api/protocols/{id}/ocr`, then review) is open for the marked
pages. Until a reviewer has accepted their OCR text and the Protocol has been
analysed again from it, readiness carries `source_page_requires_ocr` naming
those pages, so the Protocol is not guidance-ready. No gate acknowledgement
clears it.

No OCR engine is selected by a client. At startup the server builds the
adapter from the environment (`src/voiney_lab/protocol_ocr_providers.py`) and
injects it as `app.state.protocol_ocr_provider`; an adapter a deployment has
already injected is kept. Two engines are supported, both over REST with
`requests`:

| `VOINEY_LAB_OCR_PROVIDERS` entry | Engine | Credentials |
| --- | --- | --- |
| `clova` | NAVER CLOVA OCR (General, V2), Korean print and handwriting | `VOINEY_LAB_CLOVA_OCR_INVOKE_URL` (https), `VOINEY_LAB_CLOVA_OCR_SECRET` (sent as `X-OCR-SECRET`) |
| `google` | Google Cloud Vision `DOCUMENT_TEXT_DETECTION`, English | `VOINEY_LAB_GOOGLE_VISION_API_KEY` (sent as `X-Goog-Api-Key`, never in the URL) |

Only the pages the extraction marked `ocr_required` are rendered (PyMuPDF,
300 dpi PNG, in memory) and sent; the other pages keep their text layer and are
labelled `pdf-text-layer`. With both engines configured each such page goes to
both: CLOVA's text is used when Hangul is at least 30% of the letters it read,
otherwise Google's; if one engine fails or times out the other's text is used;
if the two read different numbers or units the page carries
`numeric_review_required` and a warning in the result data, which blocks
nothing. The number comparison leaves out, where the engines give word boxes,
the page's running-footer band (a word whose top is in the bottom 8% of the
rendered page, the band the text layer uses for its footer) and, on a page
that has a text layer, words whose centre is on none of the PDF's own text
blocks -- letters drawn inside a picture. A scan has no text blocks, so there
only the footer band is left out. The chosen text is the engine's whole text
either way. On real readings (2026-10-05, lane P3) this cleared the mark on
ANKOM pages 2, 3 and 9, where it came only from a footer date and words in a
photo, and kept it on the scanned reagent-kit pages 2–4, where the engines
read body values differently; a changed body number still sets it. With one engine configured, that engine is used alone. With none,
the endpoint returns `protocol_ocr_not_configured` and preserves the immutable
PDF. Every page records the engine and version that produced it. Keys,
secrets and the invoke URL are never logged or returned. These adapters are
contract-tested against fake transports in the test suite, and live-tested on
2026-10-05: both engines answered real requests for the scanned reagent-kit
guide (4 pages) and three ANKOM pages, and the OCR -> accept -> re-analysis
flow ran once end to end in a measurement store. The contract they implement is in
`src/voiney_lab/protocol_ocr.py`. Credentials and provider choice stay outside
HTTP input and the voice execution path.

Missing provider configuration is persisted as an actionable failure with retry;
it is not displayed forever as an unexplained `analysis_required` state.
Unsupported conditions, ambiguities, critical missing values, conflicts,
unreviewed/invalid OCR, corrupt/encrypted PDFs, and unsafe files fail closed.

Long text-native onboarding is evidence-first. Documents over eight pages, or
documents that exceed the existing single-pass request envelope, are split into
page-aligned chunks without lowering the 192 KiB per-chunk text ceiling. Each
established eight-core-page window is further subdivided at a deterministic
4 KiB core-source target to bound expected claim-output cardinality; an atomic
page is never split. The provider returns a small claim DTO rather than one
`ExperimentProtocol` per chunk and selects compact request-scoped evidence
handles. The server resolves those handles to immutable source identity and exact
contiguous excerpts. Deterministic validation rejects a whole chunk on any
fabricated, stale, non-contiguous, or out-of-scope evidence; merge requires every
planned chunk and complete page coverage before whole-document consistency
validation and final domain assembly.
Chunk calls are serial by default, concurrency two is explicit/experimental, and
the 120-second limit is one total-run deadline rather than a fresh timeout per
batch. No partial-success result is persisted as a review candidate.

## Lab adaptations

A local protocol difference is represented as a new immutable child revision, never
as an edit to an imported original. The adaptation record pins the exact base
and adapted revision IDs and accepts only step-linked equipment differences,
reagent substitutions, lab notes, and troubleshooting tips. Equipment/reagent
changes require explicit before/after values and a rationale.

Every adaptation begins `review_required`, appears in the existing source-review
inbox, uses the existing diff view, and becomes executable only after the
existing reviewer/admin approval event. A development-status source cannot be
approved directly; an explicit lab-adaptation child must be reviewed. Rejected,
revoked, stale, or already adapted revisions fail closed.

The tenant-scoped API is:

- `POST /api/workspace/protocols/{base_revision_id}/adaptations`;
- `GET /api/workspace/protocol-adaptations`; and
- `GET /api/workspace/protocol-adaptations/{adapted_revision_id}`.

Approval and revocation continue through
`POST /api/workspace/reviewer/revisions/{revision_id}/decision`; there is no
parallel approval mechanism.

## Korean STT reliability

The input preference is `AUTO`, `KOREAN`, or `ENGLISH`; the browser defaults to
Korean for this deployment. Korean mode sends `language=ko`, `format=true`, and
bounded scientific/protocol key terms to xAI. The official API documents that
`language` enables formatting; it does not force the model to transcribe in that
language. The response’s detected BCP-47 language is therefore treated as
evidence, not as a guarantee.

When Korean is selected and the detected language or script conflicts with the
preference, transcript admission emits the fixed clarification:

```text
음성 인식 언어가 불확실합니다. 다시 한 번 말씀해 주세요.
```

No workflow mutation is executed from that mismatched transcript. Sanitized
analytics retain only the mismatch classification and timing—not transcript
text. See the [official xAI STT documentation](https://docs.x.ai/developers/model-capabilities/audio/speech-to-text).

### Speech providers

The server cuts each utterance with its WebRTC VAD and sends the finished
utterance (16 kHz mono PCM16) to one batch STT request; replies are spoken one
sentence segment at a time, each segment one TTS request for 16 kHz PCM16.
`src/voiney_lab/voice_providers.py` lets each of those two calls use a
different provider. The defaults keep the original xAI path unchanged.

| Setting | Values | Default |
|---|---|---|
| `VOINEY_LAB_STT_PROVIDER` | `xai`, `google_cloud`, `elevenlabs` | `xai` |
| `VOINEY_LAB_STT_MODEL` | provider model id | xai: not sent; google_cloud: `latest_long`; elevenlabs: `scribe_v2` |
| `VOINEY_LAB_STT_LANGUAGE` | `ko`, `en`, `vi` — used when the session input language is `auto` | empty (provider detects; google_cloud then uses `ko`) |
| `VOINEY_LAB_STT_TIMEOUT_SECONDS` | 1–300 | `120` |
| `VOINEY_LAB_STT_VAD_THRESHOLD`, `VOINEY_LAB_STT_FILLER_WORDS` | xAI-only request fields (lane SV moved them from xAI-only names; `setting_renames.py` maps the old ones) | `0.5`, `0` |
| `VOINEY_LAB_TTS_PROVIDER` | `xai`, `google_cloud`, `elevenlabs` | `xai` |
| `VOINEY_LAB_TTS_MODEL` | provider model id | xai, google_cloud: not sent; elevenlabs: `eleven_v4_turbo` |
| `VOINEY_LAB_TTS_VOICE` | a voice of the selected provider | xai: `leo`; google_cloud: `ko-KR-Chirp3-HD-Charon`; elevenlabs: `JBFqnCBsd6RMkjVDRZzb` |
| `VOINEY_LAB_TTS_TIMEOUT_SECONDS` | 1–300 | `120` |
| `VOINEY_LAB_GOOGLE_SPEECH_API_KEY` | Google Cloud API key with Speech-to-Text and Text-to-Speech enabled | — |
| `ELEVENLABS_API_KEY` | ElevenLabs API key (needs speech-to-text and text-to-speech permission) | — |

A voice name belongs to one provider: change `VOINEY_LAB_TTS_VOICE` (or leave
it empty) together with `VOINEY_LAB_TTS_PROVIDER`. A Chirp 3 HD voice follows
the turn's language (`ko-KR-Chirp3-HD-Charon` speaks an English turn as
`en-US-Chirp3-HD-Charon`).

- `google_cloud` STT uses Cloud Speech-to-Text **v1** `speech:recognize`
  (`latest_long`, `latest_short`, `command_and_search`, `default`). The newest
  model, `chirp_3`, exists only in the v2 API, and v2 refuses API keys; using it
  needs a service-account credential, which is not set up. Key terms go out as
  `speechContexts` phrases.
- `google_cloud` TTS uses Cloud Text-to-Speech v1 `text:synthesize` with a
  Chirp 3 HD voice. Gemini-TTS (`gemini-2.5-flash-tts`) answered 403 with an
  API key (it needs Vertex AI permission).
- `elevenlabs` STT is `POST /v1/speech-to-text` (`scribe_v2`, key terms as
  `keyterms`); TTS is `POST /v1/text-to-speech/{voice_id}` with
  `output_format=pcm_16000`.

The pause and resume words ("잠깐", "멈춰", "정지", "일시정지", "스톱", "재개",
"다시 시작") go to every STT provider as key terms; a transcript made of them
is never dropped as a key-term dump.

**The agent's own voice (lane XO, decision 6).** Only the xAI request carries
`vad_threshold`; an empty transcript sends a barge-in candidate back to the
playback it interrupted. Google's and ElevenLabs' batch STT take no such
threshold, so a laptop speaker's sound came back as the researcher's turn.
The server remembers each sentence it synthesizes; a barge-in candidate, or a
turn begun within 3 s after playback, whose transcript (12 characters or more)
lies 80% or more inside 3-character runs of one of them is dropped, logged as
"메아리로 버림" (`self_echo`), and nothing changes. A pause or end word the
sentence did not have keeps the transcript. Echo cancellation in the browser
is the screen's.

While a turn waits, the browser plays a tone and the screen shows the state.
The spoken status sentence ("요청을 확인하고 있습니다.") is off unless
`VOINEY_LAB_CASCADE_FILLER_STATUS_SPEECH_ENABLED=true` (lane XO, decision 7).

Switching provider does not move the safety boundary: the emergency gate, the
deterministic front rules (pause/stop/end confirmation) and server validation
read the transcript the same way whichever provider wrote it. A provider error
or timeout raises exactly as the xAI path does, so an ordinary turn ends with
the turn error and no state change, and an interrupted answer asks the user to
say it again; an empty result is rejected as an empty transcript. The
adapters are contract-tested against fake transports in
`tests/test_voice_providers.py`; lane SV's measurement report records which
real calls were made.

## Step-named questions and the "2단계"/"이 단계" homophone (lane R6)

Decisions of 2026-10-06, from two voice tests:

- **A quantity asked of a named step** is answered from that step's source
  values by the `quantity_target` front rule, in the same sentence as an
  untargeted one ("3단계에서는 solution A를 500 µL 넣어요."). A step with several
  substances is asked back; a step with no value is read as written.
- **Another step's substances are said with the source's values**: "다음
  2단계는 Solution A(25mM AMBIC 2 : acetonitrile 1)와 Solution B(25mM AMBIC)를
  만들어요." Every number is the source's (a "parts" mixture is said as a
  ratio of the same numbers; a long form defined in the source may be said by
  its abbreviation). Past 70 characters the names alone are said, "자세한 값은
  화면에 있어요.", and the screen keeps the values.
- **A question back is not a refusal**: the "어느 쪽인지 말씀해 주세요" turn is
  planned with `speech_mode: control`, so it ends "complete", not "차단됨".
- **"2단계" and "이 단계" sound alike.** Away from step 2, a read-only question
  (왜·뭐·얼마나·설명) naming either is answered for the current step,
  "지금 N단계 기준으로 답할게요. … 2단계를 물으신 거면 '두 번째 단계'라고 해
  주세요." "두 번째 단계" and "다음 단계" are taken as said. A word that moves
  the protocol (완료, 이동, 시작, …) is not read this way; "2단계 완료했어" at
  step 1 still gets the step-mismatch question "현재 진행 중인 단계는
  1단계입니다. 1단계를 완료하셨다는 뜻인가요?".
- **A term answer reads as a sentence**: "탈색(destained)은 원문 7단계에
  나와요." (the Korean word the researcher used, then the source's spelling).
- **Web references off** (`VOINEY_LAB_EXTERNAL_REFERENCES_ENABLED=false`): no
  research step is announced unless it has something to show, so nothing ends
  in "웹 참고 자료 확인 제한".

## Spills, going back within a repeat, and a later start (lane R7)

Decisions of 2026-10-06, from gaps lane RP found running an in-gel day on the
rules' path. What is mechanical is a rule; a state change needs an explicit
request, a server check and a yes.

- **Spilling is an anomaly.** "흘렸어", "엎질렀어", "쏟았어", "넘쳤어" and their
  forms (엎었어, 엎어졌어, 쏟아졌어, 흘러넘쳤어, 넘쳐버렸어, 넘치고 있어, 흘린 것
  같아, …), with or without what spilled ("튜브를 흘렸어" used to be read as a
  question about tubes), are recorded as an anomaly at the current step, a
  question after the spill included ("흘렸는데 어떡해"), and under an open
  endpoint question too (the question stays open, as for "튜브가 터졌어").
  Nothing is said about what to do: no guidance the source and the approved
  safety material do not give. Spilling asked about, supposed, permitted,
  guarded against or denied ("흘렸어?", "흘려도 돼?", "쏟으면 어떡해?",
  "흘리지 않게", "안 흘렸어") stays what it was. The emergency gate is unchanged
  and still runs first.
- **Going back within a repeat** (`repeat_return` front rule). "2단계로
  돌아가", "2단계부터 다시 할게", "다시 2단계로", "2단계로 다시 가자" ask
  "2단계로 돌아갈까요?" only at the step whose source text states a repeat
  (in-gel: 2-7 at step 7, 8-9 at step 9, 17-18 at step 20) and only for an
  earlier step of that repeat; a yes moves back ("2단계로 돌아왔습니다."), a no
  or anything else moves nothing. Elsewhere nothing moves and the reason is
  said: "원문이 7단계에서 말하는 반복 구간은 2~7단계예요. 1단계는 그 안의 앞
  단계가 아니어서 이동하지 않았어요.", "원문은 7단계에서 2~7단계를 반복하라고
  해요. 앞 단계로 돌아가기는 7단계에서만 할 수 있어서 …", "원문은 12단계에서
  반복 구간을 말하지 않아요. …". A question ("2단계로 돌아가도 돼?") asks
  nothing.
  The earlier repeat policy holds: the agent says no number of rounds and
  never that a round was enough; the hand-over record
  (`human_led_repeat_disclosure`, `repeat_interval_record`) and
  `declare_repeat_interval_complete` carry no count; the step that states the
  repeat still waits on the person's observation of the endpoint.
- **A later start** (`start_at_step` front rule). Before the experiment, or at
  its first step, "10단계부터 시작해줘" asks "1~9단계는 건너뛰고 10단계부터
  시작할까요?"; a yes starts at step 10. Past the first step: "시작 단계는
  실험을 시작하기 전이나 1단계에서만 고를 수 있어요." "1단계부터 시작해줘" is the
  ordinary start.
- **What is recorded** (`CuratedProtocolTurnPlan.step_record`, written by
  `server.py`'s event mapping, in the payload as `step_record`):
  - a return: experiment-report event `repeat_returned` at the step returned
    to, with `repetition_id`, `repeated_step_labels`, `from_step`, `to_step`,
    `returns_confirmed` and `round` (`round_counted_from:
    "confirmed_returns"` -- a round run without a spoken return is not in it);
    the workspace session moves to that step (`repeat_returned`);
  - a step completed again in a later round: `step_completed` with `round`
    and `completed_before`; a step already marked completed in the workspace
    is not marked again (the durable session keeps one completion per step);
  - a later start: `session_started` then `steps_skipped`
    (`skipped_step_labels`, `skipped_step_ids`, `start_step`), or
    `steps_skipped` alone at the first step; the workspace session starts at
    that step.
- **Resuming after a return.** `restore_experiment_progress` also accepts a
  run that went back within a repeat: every step before the current one
  complete, and the steps completed past it inside that repeat. A run started
  at a later step is not resumed yet: recovery still requires every earlier
  step completed, and the server does not pass the skipped steps to it.

## The router line: history, two tools, rule conflicts, spills, safety (lane RT)

Decisions of 2026-10-06, after the 10/2 advice: the front rules' commands and
state changes must reach the LLM's history, state changes and records are one
tool each, and the front rules keep only what needs them.

- **History** (decision 2). The router's history holds the last turns in the
  order they happened, whoever handled them: the front rules' turns, the
  emergency gate's turn (the words and the state, never its reply), and the
  screen's pause and resume buttons and a run recovered on reconnect, each a
  bundle of its own (`"source": "screen"`). Each bundle says what the server
  did: `result`, `state_after` (step, status, and the step timer when it is
  running or has run out), `question_open` (the server question the next turn
  can answer) and `recorded` (the type and the words the server stored). The
  reply kept is the one that went out: the speech when it was spoken, the
  screen text otherwise. It stays bounded (6 turns, about 1,200 tokens), and
  the server snapshot each call carries stays the state
  (`llm_router.RouterHistoryTurn`, `history_turn`, `screen_history_turn`).
- **Two tools, one allow-list** (decision 3). State changes are
  `change_state(action)` and records `record_log(type)`; read-only questions
  are answered with no state tool (the `answer` function is the reply's form
  and changes nothing). `llm_router.CHANGE_STATE_RULES` and
  `RECORD_LOG_RULES` list each value with the words its evidence must show,
  what it needs running and the rules' action it runs as; the schema, the
  parser, `validate_tool_proposals` and `apply_tool_proposal` all read them,
  and a value off the list is refused wherever it arrives.
- **Rule conflicts fixed** (decision 4).
  - "재개", "계속", "resume", "프로토콜 재개/계속" are resume words only; they
    used to be start words too and began an experiment never started.
  - A resume never starts an experiment never started: "아직 실험을 시작하지
    않았어요. 시작하려면 '프로토콜 시작해줘'라고 말씀해 주세요." (a pause said
    before the start is lifted). With nothing paused it says "일시정지 상태가
    아니에요. 현재 N단계입니다." instead of claiming a resume.
  - A question about a command is not the command, as for the pause and end
    words: "시작해?", "재개?", "종료?", "재개해도 돼?" change nothing, and
    "타이머 시작했어?/할까?/해도 돼?" get the timer's state ("이 질문만으로는
    타이머를 시작하지 않았습니다."). "1단계부터 해볼까" stays a start (lane R3).
  - No timer starts on an ended experiment.
- **Spills by front rule** (decision 6). A spill, a knock-over or an overflow
  said as having happened (lane R7's reading) is the front rule
  `anomaly_report`: recorded router on or off, with no model call. Asked,
  supposed, permitted, guarded against or denied, it is handed on as before.
- **No made-up safety instruction** (decision 7). A sentence of a router
  answer that instructs (a request, a must or should, a don't, a
  recommendation) about a safety topic -- a safety data sheet, spill
  response, safety rules or officers, gloves, eye protection, a lab coat,
  protective equipment, ventilation or a hood, evacuation, rinsing skin or
  eyes, cleaning up a spill, waste disposal, medical or emergency help, fire
  -- stays only when every topic it names is in the protocol's own text or an
  approved (non-demo) safety document of the session's safety pack.
  Otherwise the sentence is taken out, the rest of the answer kept, and "이
  상황의 안전 안내는 원문에 없어요." said once
  (`answer_checks.ungrounded_safety_instructions`,
  `llm_router.without_ungrounded_safety`).

## Notes said aloud, the report's values confirmed, and a later start continued (lane N)

Decisions of 2026-10-07. With gloves on, the experiment notebook is written
by voice too, and a report's important values are confirmed by the
researcher before they go in.

- **Notes** (decision 1, front rule `note_record`). "실험노트에 적어 줘 / 노트에
  적어 줘 / 기록해 줘 / 메모해 줘 / 메모 추가해" with words -- after the command
  ("실험노트에 적어 줘, pH 7.2") or before it ("pH 7.2라고 적어 줘") -- records
  the words at the current step exactly as the STT gave them, case and
  spelling kept. Their kind is read by rule (`curated_protocol.note_kind`):
  a measurement (a number with a unit, or a named quantity such as pH with a
  number), a deviation ("원문과 다르게", "대신", "더/덜 넣었어"), an
  observation, and otherwise -- or when unclear -- a memo. The kinds are the
  router's `record_log` allow-list (`RECORD_LOG_RULES`: observation,
  measurement, deviation, memo, anomaly); a memo keeps the record's `note`
  category, and a router note stores the utterance's own span, never the
  model's spelling. Once stored the note is read back, never asked about: a
  measurement value by value ("피에이치 칠 점 이로 기록했어요"), anything else
  "N단계에 기록했어요". The command alone asks "어떤 내용을 기록할까요?". A
  completion said as a note ("완료했다고 기록해 줘") and a spill keep their
  own rules; an open endpoint question stays open under a note.
- **Corrections** (decision 2, front rule `record_fix`). "방금 기록 고쳐 줘, 7.2가
  아니라 7.4" asks "방금 기록 'pH 7.2'를 'pH 7.4'로 고칠까요?"; "방금 기록 지워
  줘" asks "방금 기록 '…'을 지울까요?". A yes appends `record_corrected` or
  `record_retracted` to the experiment report and `observation_corrected` or
  `observation_retracted` to the experiment timeline; the record itself is
  never changed. The screen (the timeline and the report's event list) and
  the report show the latest words marked 정정됨 (or 취소됨), the first
  words beside them.
- **The report's values** (decision 3, front rule `report_review`). When the
  experiment ends the server lists the record's important values
  (`experiment_reports.report_review_items`): measurements, observations
  with a number, points done differently from the source (a deviation note,
  a later start, a confirmed return, a timer ended early) and anomalies.
  With any listed it says "보고서에 넣을 중요 값 N개를 확인할게요." and reads
  them one by one, each ending "맞으면 '네'라고 해 주세요". "네" confirms;
  "고쳐 줘, X가 아니라 Y" is asked once and a yes corrects (as above) and
  confirms; "아니" asks how to correct it; "나중에 할게" -- or anything else,
  or the session closing -- leaves the rest to the screen's checklist (✓ 확인,
  고치기). Each answer is an event (`report_value_confirmed`,
  `report_review_deferred`). With nothing listed nothing is asked.
- **Timers, pauses and resumes in the report** (decision 4). On the served
  path a timer start, a pause and a resume that took effect reach the
  experiment report's ledger, so its timer column reads "원문 10분 / 실제 12분"
  and a pause "3분 동안 멈췄다가 다시 진행했다"; a second "타이머 시작해줘"
  adds nothing.
- **Words as said** (decision 5). Every path stores the researcher's words as
  the STT gave them; reading them uses the normalized key, storing does not.
- **A later start continued** (decision 9). A run opened with "N단계부터
  시작해줘" can be continued after the connection dropped, and from a
  checkpoint: the recovery is handed the steps the start skipped, from the
  record (`server._workspace_skipped_step_ids`), and accepts exactly the steps
  before the start in place of completions; a checkpoint restart carries
  them (`steps_skipped_carried_over`).

## Semantic intent fallback

Researchers code-switch and paraphrase. `타이머 얼마나 남았어?` and
`타임 얼마나 남았어?` are recognized deterministically, but `Time 얼마나 남았어?`
is the same question in a form no regex table anticipated. The semantic intent
fallback answers that class of utterance without giving a model any authority.

```text
STT
 → deterministic intent fast path            (unchanged, still the fast path)
 → semantic intent proposal                  (only when the fast path returns a catch-all)
 → server-owned policy validation            (evidence, context, and tier gates)
 → deterministic workflow state machine      (the only thing that transitions)
 → persistence
 → acknowledgement
```

The resolver may propose only an intent that already exists in the curated
action contract - current step, next-step information, complete current step,
not done, start timer, timer status, timer-operation information, pause, resume,
stop, repeat, related question, or `unknown` - and returns structured data (`intent`, `target`,
`mutation_requested`, `confidence`, `explicit_action_evidence`, `reason`), never
free-form instructions. It uses the same xAI chat boundary as the rest of the
product; no second provider is introduced.

Mutation safety is structural, not advisory:

- **Read-only intents** need only the read-only confidence floor. A timer
  question is answered from the server's own timer, so it can report "the timer
  is not started yet" but can never invent a timer the approved step does not
  define.
- **Bounded control** (start timer, pause, resume) additionally requires
  `mutation_requested`, a verbatim action span copied from the utterance, a
  actual action request rather than an informational or hypothetical question,
  an active workflow, no open confirmation gate, and the higher mutation
  confidence floor. A polite request may end in question punctuation; it still
  passes the same verbatim-evidence and server-state gates.
- **Checkpoint intents** never execute. A completion proposal is downgraded to
  the existing explicit completion confirmation, so the researcher's own answer
  commits the step. A stop proposal is refused outright: ending a run stays a
  deterministically worded command.
- Source-defined observation checkpoints, transcript-quality blocks, pending
  confirmation gates, and the deterministic non-mutating completion guards
  (hypothetical, quoted, negated, future completion) are all evaluated
  independently of the proposal and continue to win.

Failure is always closed. If the fallback is disabled, the resolver is
unreachable, the call times out, the structured output is malformed, the
proposed intent is unsupported, or confidence is below the floor, the turn keeps
exactly the outcome the deterministic path already produced. A turn the
deterministic path resolves never constructs a provider client at all, so voice
interaction never depends on the model being available.

Enable it per deployment (see `.env.example`):

```bash
VOINEY_LAB_SEMANTIC_INTENT_ENABLED=true
VOINEY_LAB_SEMANTIC_INTENT_MODEL=grok-4.20-0309-non-reasoning
VOINEY_LAB_SEMANTIC_INTENT_TIMEOUT_SECONDS=2.5
VOINEY_LAB_SEMANTIC_INTENT_MIN_CONFIDENCE=0.6
VOINEY_LAB_SEMANTIC_INTENT_MUTATION_MIN_CONFIDENCE=0.85
```

The dedicated non-reasoning model keeps this small typed classification off the
slower general reasoning path. The 2.5-second value is a hard provider boundary,
not permission to retry; the request uses no tools and caps its output at 160
tokens.

Every turn publishes a privacy-safe ruling on `turn.route_decision` under
`semantic_fallback` (`status`, `reason_code`, `proposed_intent`, `accepted`,
`confidence`, `latency_ms`) - reason codes and enum values only, never
utterance text or model prose.

## LLM router (off by default)

Decision D1 (2026-10-02, AGENTS rule 3): behind deterministic front rules,
intent judgment moves to one LLM router; a state change stays a tool proposal
that the server validates and carries out. **It is off unless
`VOINEY_LAB_LLM_ROUTER_ENABLED=true`** (no launcher sets it; turning
it on in development or the pilot is decided by the people running it, from
the lane R evaluation). Off, the voice path is exactly the one described
above. The router role's `VOINEY_LAB_ROUTER_PROVIDER` (default `xai`),
`VOINEY_LAB_ROUTER_MODEL` (default `grok-4.20-0309-non-reasoning`) and
`VOINEY_LAB_ROUTER_REASONING` choose the model (see [Model providers by
role](#model-providers-by-role)), and `VOINEY_LAB_LLM_ROUTER_TIMEOUT_SECONDS`
(default 2.5) how long a turn waits for it; it needs that provider's key.

The router and the semantic-intent fallback are never both on: with
`VOINEY_LAB_LLM_ROUTER_ENABLED=true` and
`VOINEY_LAB_SEMANTIC_INTENT_ENABLED=true` the server refuses to start and says
why (lane M1, decision 4) -- one turn is decided along one line.

On, a turn goes:

- **Front rules** — `CuratedProtocolSession.front_plan()` plans pause and
  "종료" words, a yes/no to an open question, replies while an endpoint
  question is open, an endpoint stated at a repeat-until step, timer status,
  "그거", repeat, cancel, transcript quality, a completion naming the current
  step, "끝났어", "다 끝났어" or "끝" said alone (asked about, "N단계
  완료하셨나요?"), a quantity asked with no target (the current and the next
  step's source values, said as a sentence or asked back), a quantity asked of
  a named step ("N단계 얼마나 넣어?", "다음 단계는 얼마나 넣어?", "두 번째
  단계 …"), a read-only question naming "2단계" or "이 단계" away from step 2
  (`step_homophone`, below), a return to an earlier step of a repeat
  (`repeat_return`) and a later start (`start_at_step`) with the yes to
  either (lane R7, above), a spill said as having happened
  (`anomaly_report`, lane RT), and a start of an experiment never started or
  already ended. These never wait on a model.
  While paused, a word one letter from "재개" or "다시 시작" ("제개") is asked
  about, "다시 시작할까요?"; a word with a digit ("3개") is not.
- **One model call** — otherwise `llm_router.route_turn_with_llm_router` sends
  the server snapshot, the nearby protocol steps with their facts, and the
  router history (the last turns, those the front rules, the emergency gate
  and the screen handled included; lane RT, above), and the model replies
  with exactly one call: `answer`, `change_state` (start, next, stop, pause,
  resume, start_timer) or `record_log` (observation, measurement, deviation,
  memo, anomaly; lane N), the values of one allow-list.
- **Server ruling** — a proposal is validated and carried out through the same
  branches `plan()` uses. `next` never moves on: it opens the completion
  question (or the endpoint question) and only the researcher's answer moves
  on. `stop` needs "종료" and asks "실험을 종료할까요?". `start_timer` runs the
  source duration and asks "원문은 15분입니다. 15분으로 시작할까요?" when
  another is said. An observation is recorded only when the words ask for
  it (메모, 기록, 관찰, 적어, ...); an anomaly that does not read as a problem is
  asked about first, "이상 사항으로 기록할까요?".
- **Answer checks** — an answer is used only if its numbers come from the cited
  facts, it claims no state change, writes no screen label, repeats server
  values exactly, and (for an explanation the PDF does not give) explains a
  term of the protocol in at most 120 characters with no numbers, method,
  safety or completion content. The server marks such an answer "PDF 밖
  설명이니 유의" on the screen and says "PDF에는 따로 설명이 없어요." first.
  Before these checks, a safety instruction neither the protocol nor an
  approved safety document gives is taken out of the answer and "이 상황의
  안전 안내는 원문에 없어요." said instead (lane RT, decision 7).
- **Outside-PDF explanation after the answer (lane R6, decision 6)** — where
  a question asks what a word means or why a step is done and the source does
  not say (a term the PDF does not define, a step's purpose, or a router
  answer saying the PDF does not explain it), the supplemental role
  (`VOINEY_LAB_SUPPLEMENTAL_PROVIDER` and `VOINEY_LAB_SUPPLEMENTAL_MODEL`, on with
  `VOINEY_LAB_SUPPLEMENTAL_MODEL_KNOWLEDGE_ENABLED=true`) is asked for one
  short general sentence. It must pass the same D4 checks (120 characters, no
  number, method, safety or completion). The rules' answer is said at once;
  the explanation is said after it, "PDF에는 따로 설명이 없어요. 일반적으로는
  …", only if it is ready while that answer plays, and `research.result`
  carries `source_label: "AI 일반 지식"` and `outside_pdf: true`. Late, failed
  or refused, the rules' answer stands alone. A quantity question the source
  answers never gets one.
- **Written general explanation (lane F, decision 2)** — a related question
  the spoken path above does not take ("이 단계 배경 지식 알려줘") may get a
  longer written general explanation from the same supplemental role, shown
  as "AI 일반 지식". Its request now carries the source text of
  the step the rules answered (`Step N source text: …`, at most 500
  characters), as the spoken path does, so the model knows which step is
  meant. The web search query is unchanged. D4's 120-character rule stays
  with the spoken path only.
- **Fallback** — when the model is late, fails, says nothing usable, is
  refused, or its answer fails a check, the turn takes the rules' own path,
  exactly as with the router off ("PDF에서 확인할 수 없어요." where the rules
  have no answer to a rejected one).

Every test is offline and fake-backed (`tests/router_fakes.py`); with a fake
model that proposes `next`, `stop` or `start` on every turn the front rules
hand on, no step is moved, no session ended and no protocol restarted
(`tests/test_llm_router.py`, `AdversarialModelTests`).

## Workspace identity and authorization

Workspace mode models organizations, principals, roles, memberships, ownership,
and tenant-scoped resources. Roles are `researcher`, `reviewer`, `lab_admin`, and
`organization_admin`; permissions are enforced centrally.

OIDC bearer tokens require signed `RS256` or `ES256` JWTs with matching issuer and
audience plus `exp`, `iat`, `iss`, `aud`, and `sub`. The tenant and roles come from
server-configured claims. Effective permissions are the intersection of verified
OIDC roles and active local memberships. External subjects are represented by an
opaque issuer-scoped principal ID.

The boundary is provider-neutral OpenID Connect and is compatible with Google
Workspace, Microsoft Entra ID, Auth0, and Keycloak when each issuer supplies an
HTTPS JWKS endpoint and the configured tenant/role claims. Provider setup and
claim mapping remain deployment responsibilities; the application does not add
provider-specific token shortcuts.

An allowlisted development identity provider is available only outside
`operational` scope. Operational workspace access requires a complete OIDC
configuration. Client-supplied tenant IDs are never accepted as an ownership
override. HTTP and WebSocket access share the same identity boundary.

## Protocol Source Hub

All imports produce an immutable `ProtocolSource` and a new lineage revision when
the source identity changes.

### protocols.io

- Accepts an exact DOI, protocol URL, URI, or version-qualified `/vN` identity.
- Calls `GET /api/v4/protocols/{id}` with a server-side bearer token and requests
  structured Markdown content.
- Preserves DOI, version URI, authors, license, source status, material/step
  structure, warnings, and canonical URL.
- Never upgrades an “In development” source to approved.

Official contract: [protocols.io API](https://apidoc.protocols.io/).

### Google Drive and Shared Drives

- Read-only folder allowlists; supports PDFs and Google Docs exported as PDF.
- Preserves file ID, modified timestamp, head revision where available, parents,
  owner metadata allowed by Drive, and Shared Drive identity.
- Uses `supportsAllDrives`, `includeItemsFromAllDrives`, and the Drive change-log
  cursor. The next cursor is persisted per connector/root.
- A changed file becomes a review-required revision; active revisions are never
  overwritten.

Official contracts: [Drive files](https://developers.google.com/workspace/drive/api/reference/rest/v3/files)
and [Drive changes](https://developers.google.com/workspace/drive/api/reference/rest/v3/changes/list).

### GitHub

- Read-only repository/ref/path allowlists; source content is pinned to the
  resolved commit SHA.
- Preserves repository, branch/tag, commit, path, license, and source URL.
- Webhooks verify `X-Hub-Signature-256` over the raw body with HMAC-SHA256 and a
  constant-time comparison, enforce delivery replay protection, and import only
  changed allowlisted paths.
- Imported repository content is never executed by the FastAPI process.

Official contract: [GitHub webhook validation](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries).

## Dry-lab workflow registry

Snakemake and Nextflow imports are metadata-only. The registry recognizes exact
entry points, configuration/schema/environment files, declared rules or
processes, engine metadata, repository identity, and commit. Reviewer decisions
are append-only. An explicit link connects a real tenant-owned durable
ExperimentSession, its matching source-hash-bound wet-lab lineage revision, and
one approved computational workflow revision. The researcher cockpit keeps the
link panel folded and closed under "개발 상세 정보" (the pilot market is wet-lab;
see "Researcher screen wording"), where it shows the repository, and the commit
and entry point in its developer details; the API always reports
`execution_supported:false` and `execution_started:false`.

There is no arbitrary workflow execution, validation sandbox, or Seqera launch in
this service. `SeqeraLaunchBoundary` is an integration interface for a future
separate execution plane. The registry follows the repository-structure patterns
documented by the [Snakemake Workflow Catalog](https://snakemake.github.io/snakemake-workflow-catalog/docs/snakemake.html)
and [Nextflow](https://docs.seqera.io/nextflow/workflow).

New links fail closed when the experiment is only a fabricated resource binding,
the wet-lab lineage protocol/source identity differs, the runtime revision is
incompatible with the catalog revision, the workflow is unreviewed/revoked, or
the stored metadata claims execution support. Revocation is terminal for that
immutable workflow revision; a changed workflow must be imported and reviewed as
a new revision.

## Knowledge, translations, and asset cards

The workspace store separates `ApprovedProtocolFact`, `LabTip`,
`HistoricalObservation`, and `TroubleshootingNote`. Observations and tips remain
non-authoritative until a reviewer explicitly promotes them into an approved
annotation; provenance is retained.

Translations are linked to the original revision, labeled machine-generated or
reviewed, and rejected if protected scientific numeric tokens differ from the
source. Lightweight reagent/equipment cards store tenant-scoped location,
optional photo/QR/barcode metadata, and an HTTPS SDS/source link. Location changes
produce a reviewable history rather than a hidden overwrite.

## eLabFTW write-back

`ElnConnector` is the generic boundary; `ELabFtwConnector` implements the real
eLabFTW API v2 create-then-patch contract. A write-back requires:

- a completed, tenant-owned durable ExperimentSession and its completed report;
- exact agreement between the session protocol/revision and report
  protocol/revision identities;
- the exact tenant-owned protocol lineage revision and matching source/execution
  identity;
- an enabled eLabFTW connector with an allowlisted HTTPS origin;
- explicit user confirmation; and
- a unique idempotency key reserved before the network write.

The server builds the payload from its own report store. Raw audio, full
transcripts, model reasoning, and secrets are never sent. Unpublished protocol
instructions are withheld by default. Cross-origin `Location` responses are
rejected before PATCH to prevent follow-up SSRF. See the
[eLabFTW API v2 documentation](https://doc.elabftw.net/api/v2/).

## PDF text extraction

One PDF engine reads every Protocol PDF, behind one module.

| Component | Job | Licence |
| --- | --- | --- |
| `pymupdf` (PyMuPDF / MuPDF), only through `src/voiney_lab/pdf_text_engine.py` | Page count, encryption, document metadata, page text, text blocks (coordinates, font size, bold), image positions, and page images for OCR. Runs in a child process per document. | AGPL-3.0 / commercial dual licence. Decision of 2026-10-06: PyMuPDF is the one engine; before commercialisation either a commercial PyMuPDF licence is bought or the engine is replaced behind this one module. Accepted on 2026-10-02 on the condition that it stays behind that module, so it can be replaced there without touching the rest of the server |
| `pypdf` | Not used to read Protocol PDFs. Still used by `curated_protocol.py` and by the tests' PDF fixture writers. | BSD-3-Clause |

What `experiment_protocol_pdf.py` records about a source, independently of the
engine: the file type, the 64 MiB bound (checked before the engine runs), the
SHA-256 of the exact bytes, and a refusal when the file changes or is replaced
while it is being read. A PDF whose cross-reference table MuPDF has to rebuild
is refused as malformed.

Page text is MuPDF's own characters, with one layout rule: a line that overlaps
the previous line vertically by at least half its height and starts to its
right is the same printed line and is joined to it with one space, so a step
number set apart from its instruction stays on the instruction's line. Measured
on 2026-10-02 over the four local sources (33 pages), PyMuPDF and the previous
engine (pypdfium2) gave the same characters on every page; only whitespace
differed, and the numbered-step trigger found the same labels on every page.

There is no engine cross-check any more. Until 2026-10-02 pdfium's text was
compared with poppler's `pdftotext`, unmapped glyphs were read back from the
document's ToUnicode map, one page's disagreement refused the whole document
(`source_text_cross_check_failed`), and a host without `pdftotext` sent every
source to a reviewer (`source_text_cross_check_unavailable`). All of that is
removed; the two readiness codes are no longer produced. Evidence still has to
appear verbatim on its cited page — that check now runs against PyMuPDF's text,
with the same whitespace normalization as before.

A page with no text layer (a scan), one with any U+FFFD (a glyph the PDF gives
no Unicode mapping), or one where at least 5% of the visible characters are
private-use or unassigned code points, is marked `ocr_required` with
`ocr_reason` `no_text_layer` or `unreadable_glyphs`. U+FFFD characters are left
out of the page text and its blocks -- never replaced -- and the page warning
says how many. One is enough: on ANKOM page 3 two such glyphs (code 0 of a
Type3 font, its missing-glyph box, absent from its ToUnicode) stand directly
before a duration value, where the previous engine dropped them silently. The
document is not refused; the extraction lists those pages in
`ocr_required_page_numbers` and in a warning. Text blocks are recorded on each
page for the next structure measurement; nothing decides on them yet.

Changing the engine changes page text byte for byte, so it changes every page
hash and every evidence segment ID (`seg-…`) derived from it. Analyses stored
before 2026-10-02 cite pdfium-derived IDs and page hashes; whether they are
re-analysed or re-pointed is an open decision, not something this change does. The curated Candidate A timer manifest was
re-pointed to the new segment IDs (same pages, same literals, same
non-whitespace segment text).

## Setup

Python 3.12 or newer is required.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
cp .env.example .env
```

Create the approved safety catalog and choose absolute, ignored runtime data
directories. Configure `.env`, then start:

```bash
uvicorn voiney_lab.server:app --host 127.0.0.1 --port 8000
```

The optional safety-handoff worker remains separate from the low-latency voice
loop:

```bash
python -m voiney_lab.worker
```

### Development on macOS

macOS is supported for development only; the server, pilot labs and CI run
on Linux. One difference matters:

- **On macOS the PDF child process has no memory cap.** On Linux the PDF
  engine's child process caps its address space (`RLIMIT_AS`, 1 GiB to read,
  2 GiB to render) and a refused cap fails the request. macOS refuses that
  cap (`ValueError: current limit exceeds maximum limit`), so there alone the
  child reads without one. The time limit and the output size limit still
  apply. Any other error, or a refusal on any other platform, still fails the
  request. See `_cap_address_space` in `src/voiney_lab/pdf_text_engine.py`
  (decision of 2026-10-04).

### Launchers

Two wrappers around that same `uvicorn` process exist so the two runtime
profiles are not assembled by hand. Both bind `127.0.0.1` by default and take
`HOST` / `PORT` from the environment; `server.py` calls
`load_dotenv(..., override=False)`, so what a launcher exports wins over the
same key in a repo-root `.env`. Their optional settings are only defaults
(lane XO, decision 1): a name already set in the shell is left alone, and a
name written in the `.env` keeps the file's value
(`configuration.launcher_defaults`). The settings a launcher fixes for safety
-- the paths it verified, the analysis model the development launcher clears,
the pilot's safety catalog, usage scope and test mode -- stay fixed.

```bash
./scripts/run_dev.sh                 # development, port 8000
./scripts/run_dev.sh --bootstrap-only  # load the curated fixture, do not serve
./scripts/run_dev.sh --test-mode       # also skip execution readiness gates
./scripts/run_dev.sh --check-only      # print the settings, touch nothing under data/runtime
./scripts/run_pilot.sh               # controlled pilot, port 8080
./scripts/run_pilot.sh --check-only  # print the configuration, do not serve
```

`scripts/run_dev.sh` is the full development launcher: it verifies the
Candidate A fixture and its externally licensed source PDF by SHA-256 and
loads the curated fixture. It names no model: each role keeps its default
below unless the `.env` chooses one. That includes the analysis role: a PDF
registered in the browser is analysed straight away with
`VOINEY_LAB_ANALYSIS_PROVIDER`/`_MODEL`/`_REASONING` (lane PA, 2026-10-06; the
launcher used to clear the analysis model). The same PDF uploaded again never
calls an analysis that already ended; a failed one runs again only when a
person presses "분석 다시 시도". The four features only xAI provides --
external reference search, web image search, generated images and semantic
intent -- default to off. Its
`--test-mode` flag sets `VOINEY_LAB_USAGE_SCOPE=demo` and
`VOINEY_LAB_TEST_MODE_SKIP_READINESS_GATES=true` and says so loudly;
without the flag neither variable is set. `scripts/run_candidate_a.sh` is the
former name and now forwards to it.

`scripts/run_pilot.sh` loads no fixture, keeps its state under
`data/runtime/pilot/`, and turns every feature that reaches outside the
approved source documents off — `VOINEY_LAB_EXTERNAL_REFERENCES_ENABLED`,
`VOINEY_LAB_SUPPLEMENTAL_MODEL_KNOWLEDGE_ENABLED`, `VOINEY_LAB_WEB_VISUAL_SEARCH_ENABLED`,
`VOINEY_LAB_GENERATED_VISUALS_ENABLED`, `VOINEY_LAB_SEMANTIC_INTENT_ENABLED` —
along with `VOINEY_LAB_MOSS_ENABLED`. Each keeps a value the operator set in
the shell (and, but for MOSS, wrote in the `.env`), so enabling one is a
deliberate act taken before startup. Test
mode is forced off whatever the environment said. Dry-lab workflows and the
eLabFTW ELN write-back have no flag of their own: both sit behind the
commercial workspace that the reviewer inbox and experiment timeline also
need, and both stay inert until an admin configures and verifies a connector,
so the launcher reports them rather than disabling them.

The pilot's approved safety documents are fixed by the launcher, never by a
`.env`. It exports `VOINEY_LAB_SAFETY_CATALOG` as the absolute path
of `data/runtime/pilot/approved_safety_catalog.sqlite` and
`VOINEY_LAB_USAGE_SCOPE=reference_only`; a different value already
exported in the shell is reported with `[WARN]` and ignored. `reference_only`
is the one scope a pilot without an identity provider can run: `operational`
needs OIDC, without which every `/api` and `/ws` request fails identity
resolution, and `demo` and `test_only` need a document of that scope in the
catalog, which is demo material by definition. Under `reference_only` the
server uses development identity and tells the model the material is
non-operational and must not be described as an approved procedure.

Before it serves, and under `--check-only`, the launcher counts the catalog
read-only and refuses with exit status 1 when the file is missing, cannot be
read as a catalog, holds any demo document (scope `demo`/`test_only`, or a
title containing "fictional", in any approval state), or has no approved,
active `reference_only` document. `--check-only` exits 0 only when none of
these holds, so a checkout without a reviewed catalog reports exit 1;
`docs/APPROVED_DOCUMENT_OPERATIONS.md` §4 describes how to put one in place.

The development launcher differs on each of these points. `run_dev.sh` takes
the safety catalog and scope from the environment or `.env` (`--test-mode`
sets the scope to `demo`), keeps its state under
`data/runtime/candidate-a-live-acceptance/`, and serves on port 8000. With no
catalog file in the `demo` or `test_only` scope, its step safety card falls
back to the fictional records in
`data/fixtures/approved_safety_manual.demo.json`; under `reference_only` or
`operational` there is no fallback and the card keeps only the step's own PDF
warning. The pilot never reaches that fallback, because it does not start
without its catalog.

### Setting names

Every setting the code reads carries the `VOINEY_LAB_` prefix (decision of
2026-10-04). `src/voiney_lab/setting_names.py` lists each one with its `.env`
area, default and meaning, and a test keeps that table equal to the names the
code reads. Names other software defines keep their own (`XAI_API_KEY`,
`XAI_BASE_URL`), as do the launchers' `HOST` and `PORT`.

Nothing reads an old name. The server, the handoff worker, the launchers and
pytest refuse to start while one is set in the environment or the repository
`.env`, and say how many and which -- names only, never a value. Rename them in
a `.env` with `python scripts/migrate_env.py --check` (names only, writes
nothing) and then `--write`, which backs the file up as
`.env.bak-YYYYMMDD-HHMMSS` (mode 600) and rewrites it grouped by area with every
value unchanged. `docs/MIGRATION_NOTES.md` records the change.

### Model providers by role

Decision of 2026-10-04 (lane M1): each role names its provider, model and
reasoning, and the people running the pilot choose them from our evaluation
sets -- performance first, cost second. Every default is the provider and
model the code called before, so an environment that sets none of these
behaves as before.

| Role | Used by | Default provider · model |
|---|---|---|
| `ROUTER` | the LLM router's one call per voice turn (tools) | `xai` · `grok-4.20-0309-non-reasoning` |
| `ANSWER` | the brain's answers, the approved-document answer, the multi-brain roles, the hand-off worker | `xai` · none: the brain requires it, the worker uses `grok-4`, the multi-brain roles `grok-4.6` |
| `TRANSLATION` | a revision's Korean and the reader translation of a step | `xai` · `grok-4.6` |
| `ANALYSIS` | the structured PDF protocol analysis | `xai` · none (required), reasoning `high` |
| `REPORT` | the experiment report's prose | `xai` · `VOINEY_LAB_SUPPLEMENTAL_MODEL`, then `grok-4.6` |
| `SUPPLEMENTAL` | outside-the-PDF explanations and the web reference search | `xai` · `grok-4.6`, reasoning `low` |

**Answer role in the voice turn, and step translation (lane F, decision 1,
2026-10-06).** The voice turn's multi-brain Answer role is used only when its
answer is back within 1.25 s, and no measured model made it (0/57), so
`run_dev.sh` now sets `VOINEY_LAB_ANSWER_BRAIN_ENABLED=false` by default (a
value in the shell or `.env` wins; `true` brings the role back, unchanged).
The Source and Visual roles still follow `VOINEY_LAB_MULTI_BRAIN_ENABLED`.
`run_pilot.sh` sets neither and follows the `.env`. Step translation -- the
automatic reading of a step with no reviewed translation ("이 단계 읽어줘")
and generating an authorized revision's translations -- no longer follows the
answer role: it is on whenever the `TRANSLATION` role has its provider's key
(and, for a revision, the workspace is on). `run_dev.sh --check-only` prints
`answer_brain:` and `step_translation:`.

Each role reads `VOINEY_LAB_<ROLE>_PROVIDER` (`xai`, `anthropic`, `openai` or
`google`), `VOINEY_LAB_<ROLE>_MODEL` and `VOINEY_LAB_<ROLE>_REASONING`
(`none`, `low`, `medium`, `high`, `xhigh`, `max`; unset leaves the provider's
default). `src/voiney_lab/model_providers.py` moves each provider's request and
reply to and from the chat-completions shape the code reads: xAI through the
OpenAI SDK as before, OpenAI through its Responses API, Anthropic through the
Messages API (with `cache_control` on the stable system blocks) and Google
through `google-genai`: the Gemini API with `GEMINI_API_KEY` by default, or
Vertex AI (now "Gemini Enterprise Agent Platform") when the SDK's own
`GOOGLE_GENAI_USE_ENTERPRISE=true` (legacy `GOOGLE_GENAI_USE_VERTEXAI`) is set
-- with `GOOGLE_API_KEY` (express mode), or with `GOOGLE_CLOUD_PROJECT` and
`GOOGLE_CLOUD_LOCATION` (default `global`) through Application Default
Credentials. The server's validation of a proposal,
its answer checks and the translation checks are the same for every provider.
A `json_schema` response format is sent in the form each provider takes:
OpenAI's strict form (every property required, an optional one nullable, the
nulls it forces dropped from the reply), Anthropic's and Gemini's grammar with
`oneOf` as `anyOf` -- and, for a schema over Anthropic's documented limits (24
optional, 16 union-typed properties) or over 100 properties on Gemini (the
PDF analysis schema on both), the schema as a system instruction instead,
with the reply validated by the server as always.
The web reference search is xAI's `web_search` tool, so
`VOINEY_LAB_EXTERNAL_REFERENCES_ENABLED=true` with any other
`VOINEY_LAB_SUPPLEMENTAL_PROVIDER` is refused at start-up. The adapters are
contract-tested against fake SDK clients (`tests/test_model_providers.py`,
`tests/test_structured_output_providers.py`), not live-tested by the suite. The
analysis role was live-tested on 2026-10-05 (lane P3) through these adapters
with `claude-opus-5-5`, `claude-sonnet-5-5` and `gpt-6.1-sol`: real requests
succeeded and were validated by the server as usual. `gemini-3.8-flash` on
Vertex AI answered a short probe, then every call was refused with HTTP 403
"Spend cap breached" for the project, so Gemini is not live-tested for any
role.

The settings that named models before (the chat, worker, multi-brain,
protocol-analysis, report-writer, external-reference and supplemental model
settings, and their reasoning settings) are old names now:
`scripts/migrate_env.py` renames them, and the server refuses to start while
one is set. Two of them in one `.env` that became the same role setting stop
the tool; keep the one you want.

### The experiment report (`.docx` and `.md`)

Decisions of 2026-10-06 (lane RP): `GET /api/experiment-reports/{id}.docx`
and `.md` are an experiment report a researcher reads, not a log of the
system. Both have one structure (`experiment_reports.report_blocks`):

- a title and a run table: date, start and end in
  `VOINEY_LAB_REPORT_TIMEZONE` (default `Asia/Seoul`); 실험자, the display
  name of the signed-in person who started the experiment (blank for the
  researcher to fill where no workspace names anyone); time taken; steps
  completed n/N; completed, or stopped at which step; the protocol's
  approval state in words; and, for a test-mode run, that the readiness
  gates were skipped;
- 1 purpose and 2 background and principle, from the protocol's PDF -- or,
  when the PDF has no background, a short one from the report model's general
  knowledge, labelled "AI 일반 지식 — 출처 없음, 확인 필요" (lane N);
- 3 materials and methods: the materials and the equipment as two tables
  (the name as the protocol lists it, its use, and the source steps that
  name it), the steps done (in Korean where a translation exists, source
  values unchanged -- a stored automatic translation marked "(자동 번역)"
  with the source under it in the same cell; a step done in more than one
  round shows each round's time), and what was done differently from the
  source, from the record
  only -- among it a later start as its event records it ("10단계부터
  시작(1–9단계 건너뜀)") and each return within a repeat with its round,
  which the report says is counted from returns confirmed in words, not
  rounds done at the bench;
- 4 results: the researcher's measurements, deviations, observations,
  memos, anomalies and photos as a table, or "기록된 관찰이 없습니다."; a
  value the researcher confirmed carries "(실험자 확인)", a corrected one
  "(정정됨 — 처음 기록 “…”)", and the values not confirmed are listed apart
  under "확인되지 않은 값" (lane N);
- 5 discussion: (가) what the record shows and (나) what needs checking are
  lists the server builds from the record, (다) review suggestions only on a
  recorded anomaly or deviation, marked as suggestions, (라) a blank
  "연구자 해석";
- 6 conclusion, the references (the protocol's PDF), and a last line saying
  whether AI (and which model) or the server wrote the sentences, and when.

Identifiers, hashes, status values and the raw event list are not in these
two reports; the event ledger and the JSON and CSV exports keep them. Every
table has fixed column shares (`experiment_reports.TABLE_WIDTHS`): short
values narrow, content wide. In Word, body text and notes run at 1.15 lines
with 4 pt after, list items 1 pt apart, table text single-spaced with none,
and headings keep 12 pt (sections) or 8 pt (subsections) above them
(`experiment_reports.DOCX_SPACING`).

A material's or a piece of equipment's steps are the server's
(`experiment_reports.item_step_labels`): it looks for the item in each source
step's text, by the name as listed without parentheses, a catalog number or
what follows a comma; failing that, by the name without its company and grade
words; and by a short name the source gives it ("Lysogeny Broth (LB)",
"ammonium bicarbonate (AMBIC)"). A step that names none of these is not
listed, and an item no step names shows "—". Its use comes from the same one
report-model call as the prose and is checked like it: a noun phrase of at
most 25 characters, no digit or unit, nothing about safety, nothing that tells
a person what to do, and only for an item the protocol lists. A use that fails
is left blank and "용도는 AI 가 원문 단계를 바탕으로 정리했다." stands under
the tables; with no model, a failed call, or no use passing, the tables have
no use column.

The report role's model writes only the prose sections, from experiment
content with no identifiers, and the server checks each before using it: a
number with a unit in the methods, results or discussion must appear with the
same unit in the record or the source (`15분` = `15 min`, `µL` = `uL`), a
bare number must appear in them, the purpose and background carry no
experiment-condition numbers and cite the PDF, nothing identifier-shaped,
quoted observations must be recorded ones, and cause suggestions must name a
recorded item. A value the researcher did not confirm may not be stated, and
a number of rounds or repetitions must come with "말로 확인한 돌아가기 기준"
(lane N). A general-knowledge background is used only when the model found
none in the source, and only with no numbers, units, citations, identifiers,
procedures or safety directions. A section that fails gets the server's own
sentence.

The report uses no outside sources. Google Search grounding was tried and
taken out, because Google's service terms for grounded results forbid caching
or storing them and modifying them or mixing them with other content, and a
report is a stored file other people read.

The prose is written once per experiment. When an experiment stops or
completes -- after the researcher has confirmed its values by voice or left
them for the screen (lane N) -- the server calls the report model on a
background thread (bounded
by `VOINEY_LAB_REPORT_WRITER_TIMEOUT_SECONDS`, 25) and keeps the reply with
the report (`experiment_report_prose`, derived output beside the ledger);
with no model configured, or when the call fails, the server's own sentences
stand. Downloads use what was kept, checked again against the record as it
is then, so a photo added later still appears in the tables.
`GET /api/experiment-reports/{id}/prose` says whether it is being prepared or
ready, and the record card shows that; `POST` to the same path writes it
again, only when a person presses "보고서 문장 다시 만들기".
`GET /api/experiment-reports/{id}/review` lists the report's values and
which were confirmed; `POST /api/experiment-reports/{id}/review/{item_id}`
with `{"action": "confirm"}` or `{"action": "correct", "text": "…"}`
confirms one after the experiment ended (a correction is appended first).
When the last one is confirmed on the screen the prose is written again --
its one model call then (lane N).

An image uploaded as evidence (`POST /api/workspace/experiments/{id}/evidence`)
puts a photo on the report at its step, captioned with the optional `caption`
parameter or else the file name. The image is never read.

### Core configuration

| Variable | Purpose |
|---|---|
| `XAI_API_KEY` | Server-only xAI credential (the default provider of every role). Needed only when a role, STT or TTS names `xai`, or one of the four xAI-only features is on: the server and both launchers refuse to start, naming the feature, when one is on without it |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY` | Credentials of the other providers, read only when a role names them |
| `VOINEY_LAB_<ROLE>_PROVIDER`, `_MODEL`, `_REASONING` | The model behind each role; see [Model providers by role](#model-providers-by-role) |
| `VOINEY_LAB_ANALYSIS_MODEL` | Required structured protocol analysis model; current deployment example: `grok-4.6` |
| `VOINEY_LAB_ANALYSIS_REASONING` | Protocol-analysis reasoning effort; defaults to compatibility-preserving `high` |
| `VOINEY_LAB_PROTOCOL_ANALYSIS_TIMEOUT_SECONDS` | Time limit of one protocol-analysis provider call, 30–3600 s; default `600`. The call runs in the background analysis task |
| `VOINEY_LAB_STT_PROVIDER`, `VOINEY_LAB_TTS_PROVIDER` | Speech providers, default `xai`; see [Speech providers](#speech-providers) |
| `VOINEY_LAB_TTS_VOICE` | Cascade voice of the selected TTS provider; xAI default `leo` |
| `VOINEY_LAB_USAGE_SCOPE` | `operational`, `demo`, `reference_only`, or `test_only` |
| `VOINEY_LAB_SAFETY_CATALOG` | Absolute approved safety-catalog path |
| `VOINEY_LAB_PROTOCOL_ENABLED` | Enables immutable PDF catalog |
| `VOINEY_LAB_PROTOCOL_DATA_DIR` | Absolute ignored protocol-store directory |
| `VOINEY_LAB_PROTOCOL_CLAIM_CHUNKS_ENABLED` | Default-off gate for controlled evidence-first claim-chunk evaluation |
| `VOINEY_LAB_WORKSPACE_ENABLED` | Enables tenant/RBAC/source workspace |
| `VOINEY_LAB_WORKSPACE_DATA_DIR` | Absolute ignored workspace directory |
| `VOINEY_LAB_ANALYTICS_RETENTION_DAYS` | Tenant default, 1–3650 days |
| `VOINEY_LAB_EXPERIMENT_REPORTS_ENABLED` | Enables append-only experiment records |
| `VOINEY_LAB_EXPERIMENT_REPORT_DB` | Absolute report SQLite path |
| `VOINEY_LAB_TEST_MODE_SKIP_READINESS_GATES` | Default `false`. Development test mode; see below |

`VOINEY_LAB_TEST_MODE_SKIP_READINESS_GATES=true` lets an analysed
Protocol be development-activated and run while readiness gates are still
outstanding. It is honoured only in the `demo`, `reference_only` and
`test_only` scopes; under `operational` it is ignored and the startup log says
so. It does not change any readiness verdict, the outstanding-gate list, the
source-evidence validation, or service approval, and a Protocol with no
analysis, or a failed one, still cannot run. While it is on, the startup log
and a page-top banner read "테스트 모드: 실행 준비 게이트를 건너뜀", an
activation made through it carries `test_mode_readiness_gates_skipped` in the
ledger, and each experiment report opened in such a session starts with a
`test_mode_readiness_gates_skipped` event. `./scripts/run_dev.sh --test-mode`
turns it on, together with the `demo` scope; `scripts/run_pilot.sh` always turns
it off, with a `[WARN]` when the environment had it on. A server started any
other way reads it from the environment or `.env`.

`VOINEY_LAB_ANALYSIS_MODEL` is read from deployment environment configuration;
there is no hidden model fallback. Protocol analysis uses `grok-4.6` in the
current deployment example. Protocol analysis explicitly defaults to high
reasoning effort. Lower effort levels remain deployment-configurable but must
pass protocol-specific completeness and evidence validation before use. The
separate low-latency semantic-intent path keeps its dedicated non-reasoning
model and timeout settings.

The browser's analysis request returns at once (`analysis_pending`) and the
provider call runs in a background task; the screen polls the status and shows
`analyzing`, then `review_required` or `analysis_failed` with its failure code.
A call that runs past `VOINEY_LAB_PROTOCOL_ANALYSIS_TIMEOUT_SECONDS` (default
600 s; measured `grok-4.6` calls took 236–501 s) fails as
`protocol_analysis_timeout` (on screen "분석 시간 초과"), apart from other
provider failures (`protocol_analysis_model_failed`); it can be retried. The
analysis request names `max_completion_tokens` 60,000, so the reply is not cut
at the adapters' chat-sized default. The response schema requires the protocol's
`before_start`, `materials`, `equipment`, `sections`, `constructs` and
`description` and each section's `steps`, so a provider cannot drop every step
by leaving the list out. A step label is supported when the cited excerpt starts
with the step number, or when the page prints that number at the start of the
line right before the excerpt; a missing or different number is refused. When
the source prints no step numbers, every step's `source_label` is empty; an
empty label on a step the page numbers is refused, and a protocol mixing
labelled and empty steps fails validation. The stored analysis keeps the empty
label, and the review screen and the run number such steps 1, 2, 3 … in order
(the review payload marks them `source_label_printed: false`). On a page whose
text is accepted OCR output, evidence comparison also joins a line break
between two Hangul letters ("날⏎짜" compares as "날짜"), like the line-end
hyphen rule: comparison only, the page text, its hash and evidence identities
are unchanged, and a break between digits is not joined. A statement that a
page cuts at its end and the next page finishes (in-gel step 24, "…which will
contain the" / "peptides.") is accepted only when it is found exactly in the
two pages joined: the body text that ends the first page -- the lowest text
block above the running footer, so a side-column duration and the footer are
not part of it -- followed by the next page's opening text. Both pages are
recorded: the evidence keeps the first page's own text, and the server adds
`continued_on_page_number` and `continued_excerpt` (the next page's own
text); a provider cannot supply either field. A continuation that is
invented, out of order, not at the page end or the next page's start, or past
the last page is refused (lane PA, 2026-10-06). A
metadata field (`created_date_evidence`, …) and a value or duration may carry
its own evidence on the page where it is printed; without it the claim is
checked on its owner's evidence page as before.

The default-off claim-chunk path supplies deterministic, bounded action-block
evidence and accepts only adjacent compact request-scoped handles from the
provider. An immutable server map resolves each handle to its full canonical
segment ID, revision/document/page hashes, segment order, and exact source text.
The provider cannot return `source_text`: the server reconstructs the exact
canonical excerpt from validated handles and uses that immutable span as
canonical claim/marker source text before running the unchanged protocol evidence
validator. Provider-authored excerpts and provider-echoed canonical hashes are
not part of the response schema. Per-request schemas require exactly one coverage
record for each core page and constrain claim, marker, and coverage page numbers
to that request's core-page set; deterministic validation independently enforces
the same rules. The shared monolithic configuration intentionally does not accept
reasoning effort `none`. If claim extraction later requires a different model or
effort policy, introduce separate `PROTOCOL_CLAIM_ANALYSIS_MODEL` and
claim-reasoning configuration instead of changing the monolithic defaults.

### Identity configuration

For operational workspace mode, configure all of:

```dotenv
VOINEY_LAB_OIDC_ISSUER=https://id.example.test/
VOINEY_LAB_OIDC_AUDIENCE=voice-workflow-agent
VOINEY_LAB_OIDC_JWKS_URL=https://id.example.test/.well-known/jwks.json
VOINEY_LAB_OIDC_TENANT_CLAIM=organization_id
VOINEY_LAB_OIDC_ROLES_CLAIM=roles
VOINEY_LAB_OIDC_NAME_CLAIM=name
```

For a non-operational local demo, omit OIDC values and optionally set a JSON
allowlist in `VOINEY_LAB_DEV_AUTH_PROFILES`. If omitted, one local
lab-admin profile is created. Do not enable development identities in operational
scope.

### Connector secrets

Connector records contain opaque `secret://` references, never credential values.
The application resolves them through a server-owned environment mapping:

```dotenv
VOINEY_LAB_SECRET_REFERENCES={"secret://tenant-a/protocols-io":"PROTOCOLS_IO_TOKEN","secret://tenant-a/drive":"DRIVE_ACCESS_TOKEN","secret://tenant-a/github":"GITHUB_INSTALLATION_TOKEN","secret://tenant-a/github-webhook":"GITHUB_WEBHOOK_SECRET","secret://tenant-a/elabftw":"ELABFTW_API_KEY"}
```

Set the referenced environment variables only in the process secret manager.
Connector `allowed_roots` constrain Drive folders/shared drives, GitHub
repository/ref/path, or an eLabFTW HTTPS origin. Live OAuth/App provisioning is an
operator responsibility; local tests use fakes.

## API surface

The browser consumes these main groups:

- `/api/protocols`: local upload, lifecycle, analysis status, evidence review,
  development activation, approval, source pages, and verified assets;
- `/api/workspace/session` and `/protocol-library`: identity-aware workspace and
  quick protocol access;
- `/api/workspace/experiments` and `/experiments/{session_id}`: tenant-owned
  experiment dashboard, recovery version, completed steps, observations,
  opaque evidence metadata, reviewer actions, and lifecycle timeline;
- `/api/workspace/protocol-adaptations`: immutable, typed lab-adaptation drafts
  linked to an exact original and the existing reviewer approval path;
- `/api/workspace/reviewer/*`: source inbox, diff, decisions, translations,
  knowledge promotion, and dry-lab review;
- `/api/workspace/admin/*`: memberships, connector configuration/check/enable,
  retention, asset cards, audit posture, tenant analytics, and pilot metrics;
- `/api/workspace/sources/*`: protocols.io, Drive, GitHub, and dry-lab import;
- `/api/workspace/webhooks/github/{connector_id}`: signed, replay-protected source
  updates;
- `/api/workspace/eln/elabftw/writeback`: confirmed experiment export; and
- `/api/experiment-reports/*`: tenant-scoped report reads/exports, and the
  report's values to confirm (`/review`, lane N).

For deployment probes, `GET /healthz` is a pure liveness check (the process
can serve a request); `GET /readyz` validates identity, workspace,
protocol-catalog, and report configuration and returns the non-secret identity
mode plus capability flags (`workspace_enabled`, `protocol_catalog_enabled`,
`experiment_reports_enabled`, `moss_enabled`). Optional external providers do
not block readiness, and a `503` means local configuration failed to parse—not
that a live external call was attempted.

All sensitive workspace APIs derive the tenant from the authenticated principal.
Connector list responses never return credential references or resolved secrets.

## Replay and voice evaluation

The A–G replay no longer relies on an ad-hoc `PYTHONPATH`:

```bash
voiney-replay
# Equivalent project-native invocation:
python -m voiney_lab.replay_turns
# The historical script remains a thin compatibility wrapper:
python scripts/replay_turns.py
```

Evaluate a sanitized JSON manifest of recognized/reference outcomes without
loading audio:

```bash
voiney-evaluate path/to/results.json
```

The manifest reports WER, semantic and command accuracy, false mutation rate,
VAD error, endpoint/barge-in latency, and repeat/correction rate. Field recordings
require an explicit consent ID and bounded retention. See
[`docs/VOICE_FIELD_EVALUATION_PLAN.md`](docs/VOICE_FIELD_EVALUATION_PLAN.md).

## Verification

Running the suite requires the `test` extra (`pip install -e '.[test]'`, which
adds `pytest` and `httpx`):

```bash
python -m pip install -e '.[test]'
VOINEY_LAB_MOSS_ENABLED=false \
VOINEY_LAB_WORKSPACE_ENABLED=false \
VOINEY_LAB_EXPERIMENT_REPORTS_ENABLED=false \
python -m pytest -q
python -m compileall -q src tests scripts
git diff --check
```

Pass those three flags in **both** baselines below. Under pytest the server
and the worker no longer read the repository `.env` (lane M1, decision 6 --
only the old-setting-name check still looks at it), but the same names
exported in the shell would still change the result; forcing the flags off per
command reproduces the documented numbers. In a clean shell (CI, a fresh
worktree) they are a no-op, so passing them is always safe and never wrong.

No test reaches a model or OCR provider, whatever keys exist: `tests/conftest.py`
removes `XAI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`,
`GOOGLE_API_KEY` and the OCR and Moss secrets from the test process before any
test module is imported, and refuses any connection to an address outside this
machine (`tests/test_no_outside_calls.py`). Provider calls stay fake-backed.

The suite needs no system PDF tools: PyMuPDF is a Python wheel, and the
`pdftotext` comparator the suite used to expect is gone (see
[PDF text extraction](#pdf-text-extraction)).

Browser acceptance coverage for the researcher/reviewer/admin workspaces (desktop
and mobile viewports) lives under `tests/e2e/` and runs separately via
[Playwright](https://playwright.dev/):

```bash
npm install
npx playwright install --with-deps chromium
npx playwright test
```

GitHub Actions (`.github/workflows/ci.yml`) runs on every push to `main`,
`dev`, `feature/**`, `fix/**` and `refactor/**`, and on every pull request into
`main` or `dev`. It has three jobs:

- **Python tests** — runs the pytest command above
  under condition B (no licensed PDF, no `.env`), `compileall`, the
  deterministic replay, and the Candidate A evaluators, which skip because the
  PDF is absent.
- **Static check (warning only)** — pylint's `possibly-used-before-assignment`
  over `src`, `scripts` and `tests`. Findings show as warning annotations on the
  run and the pull request; the job never fails the build.
- **Playwright browser acceptance** — `playwright.ci.config.ts` with
  `scripts/run_ci_server.sh`, a credential-free server launcher that runs with
  an empty protocol catalog instead of the full Candidate A demo fixture, since
  that fixture's integrity check requires an externally licensed source PDF
  that is intentionally not committed to the repository. `GET /api/protocols`
  refuses to answer without an approved safety catalog, so the job builds one
  from the repository's fictional demo manifest
  (`data/moss_demo/approved_documents.ko.json`) and passes it to the server
  with `VOINEY_LAB_USAGE_SCOPE=demo`. A failed run uploads
  `test-results/` (traces and screenshots).

Local development still uses `scripts/run_dev.sh` (the default
`playwright.config.ts`) for full-fidelity manual testing when that PDF is
available.

The same externally licensed PDF also backs 14 pytest modules' byte-exact
source-identity checks and both `scripts/evaluate_candidate_a_*.py`
evaluators. `tests/conftest.py` skips those modules (with an explicit reason)
whenever the PDF is absent, and the CI workflow does the same for the
evaluator scripts, rather than faking the file or hiding a real failure
behind it. The Playwright browser suite and `python scripts/replay_turns.py`
run identically in CI and locally; the pytest suite does **not** — see the two
baselines below.

### Two pytest baselines

The pass count depends on whether the externally licensed source PDFs are
present, so **a reported pass count is meaningless without saying which
condition produced it.** Always name the condition when quoting a number.

Both conditions use the same command, including the three flags above.

**Condition A — licensed sources present** (the maintainer's tree;
`data/runtime/candidate-a-source/in-gel-digestion.pdf` plus the ANKOM,
intracellular-metabolite and dynamic-headspace PDFs):

```
1521 passed, 1293 subtests passed in 84.91s
```

Zero skipped, exit code 0. Measured 2026-09-11 at `bfc292b`.

**Condition B — licensed sources absent** (CI, or any fresh clone or
worktree, since `/data/runtime/` is git-ignored):

```
1198 passed, 541 skipped, 1178 subtests passed in 49.49s
```

Zero failed, exit code 0. Measured 2026-10-01 at `2e59706` in a fresh clone
with a fresh virtual environment (Python 3.14, `pdftotext` installed). A fresh
clone or a detached `git worktree` is the non-destructive way to reproduce
this condition: never move, rename, or delete the real PDF to simulate its
absence.

The two totals are not directly comparable — collection is partly dynamic
(`test_stored_payloads_still_load.py` discovers stores under `data/runtime`,
finding none in condition B).

Condition B used to fail on tests that read a local PDF without checking
that it is there. They now skip with their reason printed when the source is
absent, the way their neighbours in the same files already did:

- `tests/test_extraction_cross_check.py` — four tests in
  `UnmappedCodePointDecisionTests`
- `tests/test_numbered_label_trigger.py` — five tests across
  `LocalSourceTriggerTests`, `FixtureScopeTests` and
  `FixtureScopeKnownLimitationTests`; the two-source count skips per source,
  so in-gel is still counted wherever it is present
- `tests/test_protocol_provider_diagnostics.py` —
  `test_diagnostic_is_read_only_for_server_owned_protocol_state`

Most of them read ANKOM, a PDF in the restored store under
`data/runtime/candidate-a-live-acceptance/`, so they skip wherever that
store is absent, not only where in-gel is.

The 14 modules `tests/conftest.py` does skip when the PDF is absent:

```
tests/test_candidate_a_acceptance_phase2.py
tests/test_candidate_a_final_hardening.py
tests/test_candidate_a_live_voice_generalization.py
tests/test_candidate_a_research_hardening.py
tests/test_candidate_a_websocket_integration.py
tests/test_curated_protocol_cascade.py
tests/test_experiment_reports.py
tests/test_phase3_acceptance.py
tests/test_protocol_catalog.py
tests/test_runtime_intent_routing.py
tests/test_safety_pack.py
tests/test_semantic_intent_fallback.py
tests/test_stability_and_semantic_hardening.py
tests/test_transcript_admission.py
```

Two of these carry weight out of proportion to their number:
`test_protocol_catalog.py` holds the only active test of the
"registration requires `application/pdf`" premise, and
`test_curated_protocol_cascade.py` is the largest test module in the
repository. Work that changes either premise should be verified under
condition A, because under condition B the tests that defend them are not
running.

Tests are provider-free unless explicitly marked otherwise. Connector and
eLabFTW contracts use fakes; the real adapters remain in the production code
path. The current integration classification is in the
[`Capability Matrix`](docs/CAPABILITY_MATRIX.md).

## Security and privacy boundaries

- Immutable source hashes, exact revisions, tenant bindings, central RBAC, and
  negative IDOR tests protect protocol/report/asset ownership.
- OIDC tokens are signature/issuer/audience/time validated; production does not
  fall back to a shared admin token or development identity.
- PDFs and connector documents have byte limits, strict identifiers, sanitized
  filenames, and no executable path.
- protocols.io and GitHub identifiers reject alternate origins, credentials,
  traversal, and unallowlisted roots. eLabFTW and displayed web assets enforce
  HTTPS/same-origin or SSRF controls.
- GitHub webhooks verify the raw payload before normal workspace middleware and
  fence repeated delivery IDs.
- Approval and write-back idempotency keys are append-only replay fences.
- Analytics persist allowlisted categories/dimensions only and purge according to
  tenant retention policy.
- Pilot metrics expose only tenant-scoped counts; durable counts and
  retention-bounded analytics are labeled separately.
- Evidence downloads are tenant-authorized and verified against their recorded
  byte size and SHA-256 before delivery.
- Audio diagnostics are disabled by default, bounded when enabled, and must stay
  in an ignored runtime directory.
- uvicorn's access and WebSocket lines keep each query value only as its length
  (`?search=<5 chars>`); a line in any other shape loses its query string. The
  handoff worker logs a report's location by length and SHA-256 prefix.

This is not a claim of electronic-signature, GLP/GMP, HIPAA, or other regulatory
compliance. A controlled deployment still requires an IdP, secrets manager,
encrypted backup/storage policy, facility-specific approval, validation evidence,
and user-accessibility/noisy-lab studies.

## Known limitations and deliberate non-goals

- No autonomous protocol approval or safety decision.
- No full ELN/LIMS, inventory, video hosting, or facility directory.
- No arbitrary GitHub/Snakemake/Nextflow execution in the voice server.
- No live Seqera, Google Drive, GitHub App, protocols.io authenticated import, or
  eLabFTW instance verification without operator credentials.
- No claim of field STT performance until the consented noisy-lab evaluation plan
  is executed.
- No cross-process job queue yet: PDF analysis background tasks are process-local;
  persisted lifecycle state and explicit retry make restarts visible and safe.
- SQLite storage and the single-process deployment path are suitable for a
  controlled pilot, not horizontal scaling or automatic failover.
- The browser experience is suitable for a controlled pilot, not a substitute
  for facility operating procedures or emergency systems; noisy-lab and
  accessibility field validation remain outstanding.
