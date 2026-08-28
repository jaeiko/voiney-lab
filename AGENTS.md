# AGENTS.md — Voice Workflow Agent engineering contract

Voice Workflow Agent is a hands-free laboratory workflow copilot, not a generic
chatbot. Its central authority is deterministic server-owned workflow state and
source-linked protocol evidence.

## Non-negotiable rules

1. Never invent protocol steps, quantities, units, timers, chemical properties,
   safety limits, observations, history, approval, or completion.
2. LLM output never mutates workflow state. Every mutation passes deterministic
   intent, identity, revision, observation, timer, confirmation, and safety gates.
3. Learning, audit, history, uncertainty, combined, visual, current-step, and
   state-control requests must use the shared `RequestArbitration` boundary. Do
   not add a competing intent classifier in a helper or prompt.
4. A read-only request must leave all workflow checkpoints unchanged. Combined
   “explain + next” requests stage an explicit completion confirmation.
5. Provider/model failures are visible, bounded, and non-mutating. External web
   content is supplementary, untrusted context; it never overrides protocol or
   approved safety evidence.
6. Never log/commit API keys, `.env`, raw audio, private lab PDFs, transcripts,
   user identifiers, runtime databases, or model reasoning. Logs and admin
   metrics use explicit privacy-safe allowlists.
7. Preserve canonical event schemas, exact scientific strings, source identity,
   append-only ledgers, and stale generation/turn cancellation fences.
8. The current voice product is Cascade-only. Do not document or configure a
   Native/Realtime path unless executable code and integration tests are added.

## Current architecture map

- `src/voice_workflow_agent/server.py`: FastAPI, WebSocket, Cascade voice loop,
  protocol APIs, external visual jobs, admin boundary.
- `intent_arbitration.py`: shared deterministic request classifier.
- `runtime_routing.py`: production curated-protocol routing boundary.
- `curated_protocol.py`: source-bounded plan and checkpoint state machine.
- `protocol_catalog.py`: immutable PDF/catalog lifecycle and source-linked review.
- `experiment_protocol*.py`: structured analysis model, validation, readiness,
  persistence, and fail-closed advanced constructs. A source-defined condition
  only a researcher can judge is a human-confirmation checkpoint, not a
  readiness failure; a genuinely unsettled source sentence still blocks.
- `product_labels.py`: the single Korean label table for internal status codes.
- `barge_in.py` / `barge_in_evaluation.py`: noise-aware interruption gate and its
  synthetic acceptance sweep. The gate decides whether a sound is worth ducking
  the agent for; it never decides what a workflow does.
- `speaker_attribution.py`: provider-neutral diarization segments and the
  participant-aware mutation policy. Diarization is not authentication; a
  speaker label is never identity.
- `source_presentation.py`: the single boundary where approved source text
  becomes a Korean answer. Translation lives only here, is mechanically checked
  for preserved numbers/units/identifiers, and fails closed to the exact source.
- `google_login.py`: interactive Google OIDC and invitation-controlled lab
  membership. Scaffolded and contract-tested; not live-validated.
- `web_visuals.py` / `external_references.py`: feature-gated current xAI/public
  research adapters and same-origin visual proxy.
- `experiment_reports.py`: append-only workflow event ledger and exports.
- `runtime_metrics.py`: bounded content-free route/tool/latency aggregates.
- `static/index.html`: production browser cockpit; it renders canonical server
  events and never derives state from assistant prose.

## Required change discipline

- Read the relevant files under `.agent/` before changing behavior.
- Add a production-boundary test, not only a helper test, for routing/provider/UI
  changes.
- PDF lifecycle work must test success, corrupt/unsupported input, long-running
  status, missing values, unsupported constructs, and operational approval gates.
- Provider calls must be fake-backed offline. Live tests are opt-in, bounded, and
  may never print credentials or full proprietary prompts/documents.
- Use immutable typed models for durable domain objects. Validate external data at
  ingress and use parameterized SQL.
- Keep frontend text insertion on `textContent`; external images must be rights-
  labeled, byte-validated, and same-origin proxied.

## Verification

```bash
source .venv/bin/activate
python -m pip install -e '.[test]'  # pytest + httpx are not in the runtime dependency set
python scripts/replay_turns.py
python -m pytest -q
python -m compileall -q src tests scripts
git diff --check
```

Do not weaken or delete a regression test to make a change pass. If a documented
claim is not exercised by code and tests, mark it historical or future work.

## Documentation authority

- `README.md`: current runnable product contract.
- `docs/CODEX_COMMERCIALIZATION_AUDIT.md`: finding/fix/evidence/remaining-risk
  ledger.
- `docs/CODEX_FINAL_COMMERCIALIZATION_REPORT.md`: final verification and product
  handoff for the pre-Pass-2 baseline.
- `docs/LAB_WORKFLOW_OS_IMPLEMENTATION_REPORT.md`: current phase-by-phase
  validation ledger and external integration classification.
- `docs/PRODUCTIZATION_FINAL_REPORT.md`: current productization and controlled-
  pilot handoff of record (role UX, workflow trust, operations, metrics,
  backup/recovery, evidence, documentation, and remaining commercial gates).
  `docs/COMMERCIALIZATION_PASS4_REPORT.md` retains the prior independent
  repository/CI, workflow-authority, live-integration, and deployment evidence;
  it is superseded but not invalidated. `docs/COMMERCIALIZATION_PASS3_REPORT.md`
  (Product Experience, repository
  re-rooting, pilot acceptance hardening, repository-identity cleanup) is the
  prior handoff, superseded but not invalidated.
- `.agent/architecture.md`, `product_context.md`, `evaluation_strategy.md`,
  `security_rules.md`, and `roadmap.md`: contributor design constraints.

Older phase-numbered documents are historical evidence and cannot override current
code, tests, or the documents above.
