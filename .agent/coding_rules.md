# Coding Rules & Engineering Standards

Revised 2026-10-20 (lane DOC4). The invariant rules are in `AGENTS.md`; this file
is how code is written here.

## 1. Prime Coding Directives

1. **Preserve Existing Behavior & Test Contracts**:
   - Every existing test in `tests/` represents a hardened contract.
   - Any refactoring or feature addition MUST maintain 100% backward compatibility with existing tests and canonical event schemas.
2. **Minimal, Surgical Changes**:
   - Avoid massive, unnecessary rewrites.
   - Make targeted, well-reasoned modifications with clear architectural justification.
3. **Server-Owned State & Gates**:
   - Never trust client-supplied state or LLM-hallucinated arguments for authorization, timers, or step transitions.
   - All state transitions, observation validation, and timer deadlines MUST be enforced server-side.
4. **Spoken Voice Text Cleanliness**:
   - Voice agent spoken text outputs must NEVER contain Markdown, asterisks, hash headers, bullet points, HTML tags, URLs, or code blocks.
   - Spoken text must be concise (1-3 sentences), front-loaded with the key answer/instruction.
   - New screen and voice wording follows `docs/VOICE_AND_TONE.md` (해요체, one meaning per sentence, no internal words — see `docs/GLOSSARY.md`). Some server sentences are parsed by code and pinned by tests: change the sentence and its test together.

---

## 2. Python Engineering Standards

### Language & Typing
- Use Python 3.12+ features with `from __future__ import annotations`.
- All functions, methods, and dataclasses MUST have complete type annotations.
- Use `dataclass(frozen=True)` for immutable domain models, configuration objects, and event envelopes.
- Avoid loose `dict[str, Any]` wherever typed dataclasses or pydantic/TypedDict models can be used.

### Error Handling & Fail-Safe Defaults
- Never swallow exceptions silently. Log exceptions with appropriate context using standard `logging`.
- For external API failures (e.g. a model role, STT, TTS, OCR, the web explanation):
  - Provide deterministic, graceful fallbacks (e.g., the rules' path when the LLM router is late or fails; server-owned error speech if a model fails).
  - Never crash the WebSocket connection on model or search errors.

### Concurrency & Async Safety
- Long-running or blocking I/O (file writes, SQLite transactions, subprocesses) must be run in `asyncio.to_thread` or handled via background queues.
- Use `threading.Lock` or `asyncio.Lock` when writing to shared append-only files.
- Avoid race conditions during fast speech turns: use generation IDs and sequence counters to discard stale async tool results.

---

## 3. Database & File Persistence Rules

1. **SQLite Practices**:
   - Enable `PRAGMA foreign_keys = ON` (the protocol, workspace, report, account and safety-catalog stores do).
   - Keep append-only tables append-only: add events, never update or delete them (`AGENTS.md`, rule 7).
   - Use parameterized queries (`?`) for all SQL operations to prevent SQL injection.
   - Ensure tables have `schema_version` verification on startup.
   - Use atomic transactions for multi-row state transitions.
2. **File Operations**:
   - Publish files atomically so no reader sees a partial file: write a temporary file in the same folder, flush it, then link or rename it into place (protocol source bytes: `experiment_protocol_files.ProtocolFileStore`).
   - Store all runtime files in configured directories (`data/runtime/` and the configured data paths), never hardcoding paths outside the project root.

---

## 4. Frontend & WebSocket Communication Rules

1. **Canonical Event Structure**:
   - All server-to-client events sent over WebSocket are flat JSON built by `protocol.event`: `{"type": "event.type.name", ...fields}`.
2. **Zero Inferred UI State**:
   - The UI must render workflow state exclusively from server-emitted canonical state events (e.g. `turn.state`, `experiment.report.state`), never guessing state from assistant speech transcripts.
3. **Accessibility & Responsive Design**:
   - The screen (`static/index.html`, five tabs and sheets) must work at desktop and phone widths with zero horizontal overflow.
   - Insert text with `textContent`, never `innerHTML` with server or model text.
   - Colors must meet WCAG AA contrast standards, with high-visibility indicators for timer countdowns and emergency alerts.
