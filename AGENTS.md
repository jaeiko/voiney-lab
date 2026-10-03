# AGENTS.md — Voice Workflow Agent engineering contract

Voice Workflow Agent is a hands-free laboratory workflow copilot, not a generic
chatbot. Its central authority is deterministic server-owned workflow state and
source-linked protocol evidence.

## Non-negotiable rules

1. Never invent protocol steps, quantities, units, timers, chemical properties,
   safety limits, observations, history, approval, or completion.
2. LLM output never mutates workflow state: a model may only *propose* a
   change. Every mutation -- read by the deterministic front rules or proposed
   through a tool call -- passes the server's identity, revision, observation,
   timer, confirmation, and safety gates, and is carried out by the existing
   curated state machine (`CuratedProtocolSession`), never by a second one.
3. Deterministic front rules read every turn first and never wait on a model:
   the emergency gate in `server.py`, then `CuratedProtocolSession.front_plan`
   (transcript quality, pause and "종료" words, a yes/no to an open question,
   replies while an endpoint question is open, timer status, "그거", repeat,
   cancel, and a completion naming the current step). Behind them, intent judgment belongs to one LLM
   router: it answers, or proposes one change through its two tools
   (`change_state`, `record_log` in `llm_router.py`); the server validates the
   proposal (`validate_tool_proposals`) and carries it out through
   `CuratedProtocolSession.apply_tool_proposal`. Until that router is wired and
   enabled (it is not yet), the shared `RequestArbitration` boundary and the
   curated rules remain the classification path for learning, audit, history,
   uncertainty, combined, visual, current-step, and state-control requests. Do
   not add any other competing intent classifier in a helper or prompt.
   (Decision D1, 2026-10-02.)
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

- `src/voiney_lab/server.py`: FastAPI, WebSocket, Cascade voice loop,
  protocol APIs, external visual jobs, admin boundary.
- `intent_arbitration.py`: shared deterministic request classifier.
- `runtime_routing.py`: production curated-protocol routing boundary.
- `semantic_intent.py`: bounded read-only semantic intent fallback vocabulary
  and server-owned proposal policy; it holds no mutation authority and is
  consulted only when deterministic routing returns a catch-all. Its evidence
  and target fences are shared with the LLM router.
- `curated_protocol.py`: source-bounded plan and checkpoint state machine;
  `front_plan` (the front rules) and `apply_tool_proposal` (a validated
  router proposal through the same branches).
- `llm_router.py`: the LLM router's two tool schemas and the server's
  validation of a proposal; no model call, no state, not wired yet.
- `answer_checks.py`: server checks on a model-written answer (numbers,
  state-change claims, display labels, outside-PDF explanations, server
  values), shared by every answering role.
- `protocol_catalog.py`: immutable PDF/catalog lifecycle and source-linked review.
- `experiment_protocol*.py`: structured analysis model, validation, readiness,
  persistence, and fail-closed advanced constructs.
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

In order of precedence, the same list as `CLAUDE.md`:

- `README.md`: current runnable product contract.
- `AGENTS.md` (this file) and `.agent/*.md`: contributor design constraints.
  `.agent/architecture.md` is the primary source for the current architecture.
- `docs/CURRENT_ARCHITECTURE.md`: the docs-side component, state-authority,
  persistence, and failure view.
- `docs/MIGRATION_NOTES.md`: schema history.

The ledgers and handoff reports this section used to list were superseded and
moved to `docs/archive/`; nothing under `docs/` has taken over their roles.
Older phase-numbered or `CODEX_*`-prefixed documents are historical evidence
and cannot override current code, tests, or the documents above.
