# AGENTS.md — VoineyLab engineering contract

VoineyLab (the company is Voiney) is a voice guide that lets wet-lab
researchers run their own uploaded protocol hands-free: upload → analysis and
source-evidence check → only the necessary confirmation cards → start → step
by step voice guidance (timers, questions, records, re-asks) → lab notebook
and report. It is an **MVP prototype, not field-validated**, not an ELN or
LIMS, and not a validated GLP/GMP/clinical system. Its central authority is
deterministic server-owned workflow state and source-linked protocol
evidence; models only propose.

This file is the one contract every coding agent follows (Codex reads it
directly; Claude Code imports it from `CLAUDE.md`). The deep architecture is
`.agent/architecture.md`; behaviour per feature is `docs/FEATURES.md`; the
reasons and dates are `docs/DECISIONS.md`.

## Non-negotiable rules

1. Never invent protocol steps, quantities, units, timers, chemical
   properties, safety limits, observations, history, or completion. Values
   and safety statements come only from the source (passing the
   source-evidence check) or from a value a person confirmed.
2. LLM output never mutates workflow state: a model may only *propose* a
   change. Every mutation — read by the deterministic front rules or proposed
   through a tool call — passes the server's identity, revision,
   observation, timer, confirmation and safety gates and is carried out by
   the one state machine (`CuratedProtocolSession`).
3. A turn is routed along one line: the emergency gate in `server.py`, the
   deterministic front rules (`CuratedProtocolSession.front_plan`), then the
   LLM router (`llm_router.route_turn_with_llm_router`, off by default via
   `VOINEY_LAB_LLM_ROUTER_ENABLED`), then server validation
   (`validate_tool_proposals`, `answer_check_failures`) and
   `apply_tool_proposal`. The router answers or proposes one change through
   `change_state` or `record_log`; with `VOINEY_LAB_ROUTER_FIRST` (off,
   needs the router) it also has the read-only `show_visual`, `clarify` and
   `not_for_me`. Use the router actively within this line, including the
   model or tool for the situation. The rules' own path
   (`runtime_routing.route_curated_runtime_turn`) is the path with the router
   off and the same turn's fallback when the model is late, fails, is refused
   or its answer fails a check — never a second decision beside the model's.
   A router HTTP 429 stops the turn without a fallback. Forbidden: two paths
   each deciding the same turn's state change; a parallel router, classifier
   or prompt that decides state apart from this line.
4. One of each authority: do not add a second workflow state machine, another
   `ExperimentSession` authority, a parallel execution-verdict subsystem or a
   parallel protocol store. The old `procedures.py` stack was deleted and must
   not return.
5. No approval step: a protocol is executable when its analysis (or latest
   review version) passes with no execution blocker
   (`protocol_catalog.available_for_execution`) and the experimenter presses
   the start wizard's "이 프로토콜로 시작". An administrator's "확인함" is a
   record and gates nothing. Safety statements and completion criteria are
   never written by the AI, and a reviewer cannot rewrite a safety statement.
6. A read-only request leaves every workflow checkpoint unchanged. Combined
   "explain + next" requests stage an explicit completion confirmation.
7. Provider and model failures are visible, bounded and non-mutating. An
   unset model role, STT or TTS provider fails closed by the name of the
   missing setting; nothing falls back to another provider. External web
   content is supplementary, untrusted context and never overrides the
   protocol or approved safety documents.
8. Never log or commit API keys, `.env`, raw audio, private lab PDFs,
   transcripts, user identifiers, runtime databases or model reasoning. Logs
   and metrics use explicit privacy-safe allowlists (numbers, codes, times).
9. Preserve canonical event schemas, exact scientific strings, source
   identity, append-only ledgers, and stale generation/turn cancellation
   fences.
10. The voice product is Cascade-only. Do not document or configure a
    Native/Realtime path unless executable code and integration tests exist.
11. No fake external validation: tests against a fake transport are
    "contract-tested"; say "live-tested" only when a real external request
    succeeded in this environment during this work. Never claim GLP, GMP,
    clinical or regulatory compliance, autonomous experimentation, or a
    complete external integration.

## Module map

`src/voiney_lab/` (one line each; details in `.agent/architecture.md`):

Server and voice loop
- `server.py`: FastAPI app — HTTP API, `/ws` voice loop (`voice_socket`, `ListenerSession`, `run_turn`), timers and notices, calibration, reconnection, report and admin routes.
- `protocol.py`: WebSocket message helpers; `event()` builds the flat JSON events.
- `audio.py`: 16 kHz PCM frames, WAV helpers.
- `vad.py`: WebRTC VAD endpointing, speaker level reference, noise floor and level endpoint.
- `cascade_filler.py`: the waiting cue (tone or phrase) during a slow turn.
- `voice_providers.py`: STT/TTS adapters (`google_cloud`, `elevenlabs`; `xai` in `server.py`) and the agent voice list.
- `language.py`: STT language normalisation, admission and the turn language.
- `emergency.py`: deterministic emergency recognition and fixed replies.

Understanding a turn and workflow state
- `curated_protocol.py`: `CuratedProtocolSession`, the one state machine — front rules (`FRONT_RULES`, `front_plan`), plans, tool-proposal application, timers, observations, notebook questions.
- `llm_router.py`: router settings, tools, the one model call per turn, proposal validation, answer checks, fallback; no state of its own.
- `answer_checks.py`: server checks on any model-written answer (numbers, state claims, labels, server values, outside-PDF, ungrounded safety).
- `runtime_routing.py`: the rules-only routing boundary (`route_curated_runtime_turn`).
- `intent_arbitration.py`: read-only request classification (`arbitrate_request`).
- `intent_fences.py`: verbatim-evidence, question/hypothetical and current-step fences for proposals.
- `completion_intent.py`: deterministic completion-statement classifier.
- `brain.py`: router turn history (`ConversationHistory`), protocol answer models, sentence chunking.
- `multi_brain.py`: optional read-only Answer, Source and Visual roles.
- `tools.py`: trusted turn context and record-value checks for `record_log`.
- `model_providers.py`: model roles, provider clients, retries, Gemini safety settings.
- `external_references.py`: the outside-PDF "AI 일반 지식" explanation and research-query planning.
- `web_explanations.py`: web explanation and free-licence photo (off by default).
- `drawn_diagrams.py`: on-request SVG diagram, sanitised and labelled (off by default).
- `source_figures.py`: step figures cut from the uploaded PDF by geometry.

Protocols
- `protocol_catalog.py`: the catalog — immutable sources, append-only protocol ledger, analysis and review versions, review cards, the execution rule.
- `protocol_link_import.py`: public Google Drive/Docs/Sheets link import.
- `experiment_protocol.py`: typed protocol model, validation, readiness and execution reasons.
- `experiment_protocol_analysis.py`: the structured analysis, pieces, evidence check, requote, repair.
- `experiment_protocol_pdf.py`: source validation, Word/photo conversion, page text and OCR need.
- `experiment_protocol_files.py`: content-addressed source bytes (`objects/sha256/…`).
- `experiment_protocol_store.py`: `protocol_workspace.sqlite`.
- `experiment_protocol_config.py`: protocol storage settings.
- `pdf_text_engine.py`: the only PyMuPDF user (bounded child process).
- `protocol_ocr.py`: the OCR contract and result validation.
- `protocol_ocr_providers.py`: NAVER CLOVA and Google Vision engines, place-by-place comparison.
- `protocol_claim_analysis.py`: page evidence segments (what is left of the claim path).
- `protocol_translation.py`: Korean translation of a revision's sentences with checks.
- `protocol_extraction_accuracy.py`: scores an extraction against a hand-built reference (tests only today).
- `start_check.py`: the two key values asked before the start.
- `safety_pack.py`: the step safety card from the facility safety catalog, usage scopes, `source_protocol_only`.
- `safety_documents.py`: safety-document manifest models.
- `document_store.py`: safety catalog SQLite ingestion.
- `retrieval.py`: catalog retrieval for `scripts/audit_approved_catalog.py` (not used by the server).

Records, accounts and operations
- `experiment_reports.py`: the append-only experiment record, lab notebook, report writer checks and exports.
- `report_projection.py`: flat step data for reports.
- `workspace_store.py`: tenant workspace — experiment sessions, timeline, observations, evidence, settings.
- `accounts.py`: invite-only accounts, sessions, login lock, admin flag, account erasure.
- `identity.py`: development identity, OIDC, principals.
- `pilot_metrics.py`: pilot metrics (numbers, codes, times) and their summaries.
- `runtime_metrics.py`: content-free in-process telemetry.
- `configuration.py`: bounded parsing of voice settings, launcher defaults.
- `setting_names.py`: the table of every setting and the old-name start-up guard.
- `setting_renames.py`: old → new setting names.
- `replay_turns.py`: replays sentences through the rules boundary (`scripts/replay_turns.py`).
- `voice_evaluation.py`: offline voice evaluation harness (`voiney-evaluate`).

`src/voiney_lab/static/`: `index.html` (five tabs and sheets; renders server
events, never derives state from prose), `connection.js` (lost connection),
`feedback.js` (rating), `mic-capture-worklet.js` (capture), `admin-metrics.html`
(pilot metrics), `native-bridge.js` (app device bridge, not loaded yet),
`manifest.webmanifest`, `app.css`, `icons/`.

Elsewhere: `mobile/` (Capacitor store-app shell, device features only),
`scripts/` (launchers `run_dev.sh`, `run_pilot.sh`, `run_ci_server.sh`; pilot
tools; ten diagnostic scripts there currently fail — `docs/DECISIONS.md`,
"알려진 빈틈"), `tests/` (pytest; `tests/e2e/` Playwright),
`data/fixtures/` (development fixture and evaluation sets).

## Required change discipline

- Read `.agent/architecture.md` and the relevant `.agent/` file before
  changing behaviour.
- Add a production-boundary test, not only a helper test, for routing,
  provider and screen changes. Provider calls are fake-backed; live tests are
  opt-in, bounded and never print credentials or proprietary documents.
- PDF lifecycle work tests success, corrupt/unsupported input, long-running
  status, missing values, unsupported constructs and the execution rule.
- Use immutable typed models for durable domain objects; validate external
  data at ingress; parameterised SQL; frontend text through `textContent`.
- Do not weaken or delete a regression test to make a change pass. When a
  decision changes expected behaviour, change only that assertion and say so.
- Documents: code is the truth. A behaviour change updates
  `docs/FEATURES.md` (and `.agent/architecture.md`, `docs/USER_GUIDE.md` when
  they describe it) in the same pull request; decisions, dates and lane names
  go only in `docs/DECISIONS.md`; a setting change goes in
  `src/voiney_lab/setting_names.py` and `.env.example`
  (`scripts/migrate_env.py`). `tests/test_docs_consistency.py` checks links,
  setting names, repository paths and script options in every document
  outside `docs/archive/`.

## Verification

```bash
source .venv/bin/activate
python -m pip install -e '.[test]'
python scripts/replay_turns.py
VOINEY_LAB_MOSS_ENABLED=false \
VOINEY_LAB_WORKSPACE_ENABLED=false \
VOINEY_LAB_EXPERIMENT_REPORTS_ENABLED=false \
VOINEY_LAB_LLM_ROUTER_ENABLED=false \
python -m pytest -q
python -m compileall -q src tests scripts
git diff --check
```

- The flags keep a value left in the shell from changing the result
  (`server.py` does not load the repository `.env` while pytest runs;
  `VOINEY_LAB_MOSS_ENABLED` is no longer read by any code and is kept only so
  the command stays the one every report used).
- Report a pass count with its condition:
  - **Condition A** — the externally licensed source PDFs are present
    (at least `data/runtime/candidate-a-source/in-gel-digestion.pdf`; the
    original checkout has a few more, so it runs a few more tests than a
    worktree that links only the in-gel PDF).
  - **Condition B** — none present (CI, a fresh clone): the modules in
    `tests/conftest.py` `MODULES_REQUIRING_CANDIDATE_A_SOURCE_PDF` and other
    licensed-PDF tests are skipped.
  The latest measured numbers, with dates, are in README section 7.
- Browser tests (`tests/e2e/`, Playwright) run in CI: `npm install && npx
  playwright install --with-deps chromium && npx playwright test`.

## File ownership

People's areas (who to ask before changing a file) are in
`docs/GIT_WORKFLOW.md`, "파일 담당". For every agent:

- Never read or edit `.env`; never write under `data/runtime/`.
- `docs/demo_script.md` belongs to a person and is untracked: never edit,
  commit or overwrite it; write a demo walkthrough in a new
  `docs/DEMO_WALKTHROUGH_*.md`.
- Do not read `docs/archive/` or `docs/course-archive/` unless a person asks
  about a specific past decision; they are records, not instructions.
- Change only the files a task allows; name any other file you had to touch.

## Terminology (as the code uses it)

- **Source Protocol / Protocol / Protocol Revision / Analysed Protocol**
  ("분석 통과한 프로토콜"): the uploaded document and its analyses. A passed
  analysis does not make it an SOP.
- **SOP**: only the facility-governed safety-document corpus
  (`safety_pack.py`, the approved catalog) — never a synonym for a protocol.
- **Review pending / review card / review version** (screen: 검토 대기,
  확인 카드·검토 카드, 검토본 vN): what the evidence check could not confirm,
  the one-item cards, and the registered result. Not an approval.
- **Start check** (핵심 값 확인), **execution blocker / notice**
  (`execution_blockers`, `execution_notices`), **"확인함"** (PI
  confirmation, non-gating).
- **Lab notebook** (실험 노트): the report's form.
- User words, screen words and code names side by side: `docs/GLOSSARY.md`.
  New wording follows `docs/VOICE_AND_TONE.md`.

## Documentation authority

Code and tests first: where a document and the code disagree, the code is
right and the document is fixed. Then, in order:

1. `README.md` — what the product is, how to run it, the doc map.
2. `AGENTS.md` (this file) and `.agent/*.md` — the contract;
   `.agent/architecture.md` is the one deep description of the architecture.
3. `docs/FEATURES.md`, `docs/DECISIONS.md`, `docs/GLOSSARY.md`, then the
   operating guides listed in `docs/DOCUMENTATION_INDEX.md`.
4. `docs/MIGRATION_NOTES.md` — schema and setting history.

`docs/archive/` holds superseded documents; they cannot override any of the
above.
