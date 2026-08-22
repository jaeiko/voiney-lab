# Voice Workflow Agent

Voice Workflow Agent is a voice-first laboratory workflow copilot. It turns a
reviewed protocol PDF into source-linked, step-by-step guidance; records explicit
observations and timers; answers bounded protocol questions; and prepares an
auditable handoff when work is blocked.

This repository is a commercially oriented prototype, not a validated medical,
clinical, GLP/GMP, or safety-control system. It does not approve a protocol,
declare an area safe, or authorize work to resume. Operational deployments still
require facility approval, identity/access controls, validation, retention policy,
and ELN/LIMS integration.

## Current product contract

- The only active voice path is **Cascade**: browser PCM → WebRTC VAD → xAI STT →
  deterministic/shared routing → grounded response or tool → xAI TTS.
- The default TTS voice is `leo`, presented as a calm professor/research mentor.
  `TTS_VOICE` is the single configuration override.
- Workflow mutation is server-owned. Model prose cannot advance a step, start a
  timer, record an observation, submit a report, or resume blocked work.
- Protocol learning, audit, history, uncertainty, combined “why + next” requests,
  visual requests, current-step requests, and state commands share one intent
  arbitration boundary.
- Read-only requests do not mutate workflow state. A combined explanation and
  “next step” request explains and previews first, then waits for an explicit
  completion confirmation.
- Responses and visuals stay linked to source identities, pages, evidence IDs,
  revisions, and canonical server events.

There is no xAI Realtime/Native speech path in the current codebase. Older design
documents that describe one are historical; active configuration and tests use
Cascade only.

## Architecture

```text
Browser AudioWorklet (16 kHz PCM)
  → WebSocket /ws
  → FrameBuffer + WebRTC VAD + interruption gate
  → xAI /v1/stt
  → shared RequestArbitration
      ├─ emergency / deterministic workflow gates
      ├─ curated protocol runtime router
      ├─ approved references / bounded external research
      └─ general agent tool loop
  → sentence-segmented xAI /v1/tts (Leo by default)
  → canonical events + browser playback

PDF upload
  → bounded PDF extraction and byte identity
  → explicit structured analysis
  → source-linked review
  → human/facility approval, or explicit non-operational development activation
  → executable catalog revision

Canonical workflow events
  → append-only SQLite experiment report
  → JSON / Markdown / CSV / DOCX export
  → privacy-minimized admin aggregates
```

The main production routing boundary is
`src/voice_workflow_agent/runtime_routing.py`. The shared classifier is
`src/voice_workflow_agent/intent_arbitration.py`; legacy intent helpers are
compatibility projections over that classifier.

## Protocol PDF onboarding

1. Select a PDF in the browser.
2. `POST /api/protocols?filename=...` streams the PDF to a bounded temporary file,
   validates it, calculates its SHA-256 identity, and stores the immutable source.
3. The browser explicitly requests `POST /api/protocols/{id}/analysis` and polls
   persisted status for long documents.
4. `GET /api/protocols/{id}/review` shows source filename/hash/page count,
   prerequisites, materials, equipment, sections, steps, sub-actions, quantities,
   timers, warnings, missing values, advanced constructs, and readiness reasons.
5. Operational scope requires the existing service-authorized approval endpoint.
   `activate-development` is available only in `demo`, `reference_only`, or
   `test_only`; it fails closed in `operational`.

Ambiguous values, missing execution-critical values, unsupported conditionals,
conflicts, corrupt PDFs, encrypted PDFs, and oversized uploads do not become
executable guidance.

## External research and visuals

External research is feature-gated and domain-bounded. It is supplementary
reference context, never protocol evidence and never an authority for workflow
mutation.

For explicit visual intent, the server tries a bounded public scientific catalog
first and makes at most one paid xAI image-search request only if needed. Displayed
external images must include a rights label, pass URL/SSRF and image-byte checks,
and be served through `/api/web-visuals/{sha256}`. Otherwise the UI shows only the
cited source page. Remote image hotlinking is rejected.

The xAI request shape was checked against the current official documentation on
2026-08-22:

- [Web search and `enable_image_search`](https://docs.x.ai/developers/tools/web-search)
- [Tool usage details](https://docs.x.ai/developers/tools/tool-usage-details)
- [Citations and `no_inline_citations`](https://docs.x.ai/developers/tools/citations)
- [Speech-to-speech model and voices](https://docs.x.ai/developers/model-capabilities/audio/speech-to-speech)
- [Text-to-speech `voice_id`](https://docs.x.ai/developers/model-capabilities/audio/text-to-speech)

## Operations and privacy

`GET /api/admin/metrics` is unavailable until
`VOICE_WORKFLOW_AGENT_ADMIN_TOKEN` is configured and requires the
`X-Voice-Workflow-Admin-Token` header. The browser’s closed admin panel uses the
token for one request and clears the input.

The endpoint exposes only aggregates: report status/completion, workflow event
counts, common blocked step labels, protocol lifecycle counts, route/intent/tool
counters, and bounded average/p95 timing samples. It excludes audio, transcripts,
free-form user wording, session/report identifiers, protocol titles, prompts, and
model reasoning. Runtime samples are in memory and bounded; persisted report
metrics come from the configured SQLite ledger.

Audio is not retained by default. Optional STT diagnostics are explicit opt-in,
bounded, and must use an ignored runtime directory. Do not enable them for private
or regulated lab work without an approved retention and access policy.

## Setup

```bash
cd voice-workflow-agent
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
cp .env.example .env
```

Set your own values in `.env`; never commit `.env`, API keys, audio, private PDFs,
or runtime databases. At minimum, configure the xAI models/credential and the
required server policy paths/scope shown in `.env.example`.

Start the app:

```bash
source .venv/bin/activate
uvicorn voice_workflow_agent.server:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`. The browser requires microphone permission and a
secure context outside localhost.

The optional safety-handoff worker is separate from the low-latency voice loop:

```bash
source .venv/bin/activate
python -m voice_workflow_agent.worker
```

## Useful configuration

| Variable | Purpose |
|---|---|
| `XAI_API_KEY` | Server-only xAI credential |
| `XAI_BASE_URL` | OpenAI-compatible xAI API root |
| `CHAT_MODEL` / `WORKER_MODEL` | Agent and worker models |
| `PROTOCOL_ANALYSIS_MODEL` | Structured PDF analysis model |
| `TTS_VOICE` | Cascade TTS voice; default `leo` |
| `VOICE_WORKFLOW_AGENT_USAGE_SCOPE` | `operational`, `demo`, `reference_only`, or `test_only` |
| `VOICE_WORKFLOW_AGENT_PROTOCOL_DATA_DIR` | Protocol catalog/object storage root |
| `VOICE_WORKFLOW_AGENT_SAFETY_CATALOG` | Approved safety catalog path |
| `EXTERNAL_REFERENCES_ENABLED` | Domain-bounded text research gate |
| `WEB_VISUAL_SEARCH_ENABLED` | Explicit web-image research gate |
| `VOICE_WORKFLOW_AGENT_EXPERIMENT_REPORTS_ENABLED` | Append-only experiment report gate |
| `VOICE_WORKFLOW_AGENT_ADMIN_TOKEN` | Guard for privacy-minimized admin metrics |

See `.env.example` for the complete, sanitized configuration surface.

## Verification

```bash
source .venv/bin/activate

# Acceptance replay for the required A–G request families
python scripts/replay_turns.py

# Focused commercial hardening checks
python -m pytest \
  tests/test_runtime_intent_routing.py \
  tests/test_commercial_protocol_fixtures.py \
  tests/test_protocol_catalog.py \
  tests/test_web_visuals.py \
  tests/test_runtime_metrics.py \
  tests/test_frontend.py -q

# Complete offline suite and static checks
python -m pytest -q
python -m compileall -q src tests scripts
git diff --check
```

Automated tests use local fakes and must not require a provider credential. Live
provider tests are opt-in and must remain bounded.

## Product status and next gates

The current build is suitable for controlled demos, usability studies, and
non-operational pilots with fictional or approved non-sensitive documents. Before
a real regulated deployment, complete SSO/RBAC, tenant isolation, secrets
management, encrypted storage/backups, retention/deletion controls, formal
computer-system validation, electronic signatures, facility-specific emergency
policy, observability export, accessibility testing with users, noisy-lab voice
evaluation, and validated ELN/LIMS/instrument connectors.

The recommended commercial wedge is an integration-light pilot for 5–20 bench
scientists using one repetitive, low-hazard workflow, measured on time-to-first
executable protocol, documentation completeness, correction rate, time saved, and
blocked-step frequency. Do not market autonomous science or autonomous safety.

## Current reports

- [Commercialization audit](docs/CODEX_COMMERCIALIZATION_AUDIT.md)
- [Final commercialization report](docs/CODEX_FINAL_COMMERCIALIZATION_REPORT.md)
- [Moss retrieval boundary](docs/MOSS_RETRIEVAL.md)
- [Approved document operations](docs/APPROVED_DOCUMENT_OPERATIONS.md)

Older phase-numbered and Candidate A reports are retained as historical evidence;
where they conflict with this README or the two current reports above, the current
documents and executable code are authoritative.
