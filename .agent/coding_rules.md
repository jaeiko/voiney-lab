# Coding Rules & Engineering Standards

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

---

## 2. Python Engineering Standards

### Language & Typing
- Use Python 3.12+ features with `from __future__ import annotations`.
- All functions, methods, and dataclasses MUST have complete type annotations.
- Use `dataclass(frozen=True)` for immutable domain models, configuration objects, and event envelopes.
- Avoid loose `dict[str, Any]` wherever typed dataclasses or pydantic/TypedDict models can be used.

### Error Handling & Fail-Safe Defaults
- Never swallow exceptions silently. Log exceptions with appropriate context using standard `logging`.
- For external API failures (e.g. xAI LLM, TTS, STT, PubChem, Moss):
  - Provide deterministic, graceful fallbacks (e.g., fallback to SQLite search if Moss is unavailable; return server-owned error speech if LLM fails).
  - Never crash the WebSocket connection on model or search errors.

### Concurrency & Async Safety
- Long-running or blocking I/O (file writes, SQLite transactions, subprocesses) must be run in `asyncio.to_thread` or handled via background queues.
- Use `threading.Lock` or `asyncio.Lock` when writing to shared append-only files (e.g. `reports/inbox.jsonl`, `processed.txt`).
- Avoid race conditions during fast speech turns: use generation IDs and sequence counters to discard stale async tool results.

---

## 3. Database & File Persistence Rules

1. **SQLite Practices**:
   - Always enable `PRAGMA foreign_keys = ON`.
   - Use parameterized queries (`?`) for all SQL operations to prevent SQL injection.
   - Ensure tables have `schema_version` verification on startup.
   - Use atomic transactions for multi-row state transitions.
2. **File Operations**:
   - When writing JSON status files or EML artifacts, write to temporary files first (`.filename.tmp`) and atomically rename (`os.replace`) to prevent partial reads by concurrent processes.
   - Store all runtime files in configured directories (`reports/`, `outbox/`, `data/runtime/`), never hardcoding paths outside the project root.

---

## 4. Frontend & WebSocket Communication Rules

1. **Canonical Event Structure**:
   - All server-to-client events sent over WebSocket must adhere to the standard envelope:
     ```json
     {
       "type": "event.type.name",
       "data": { ... },
       "session_id": "...",
       "timestamp": 1724150000.0
     }
     ```
2. **Zero Inferred UI State**:
   - The UI must render workflow state exclusively from server-emitted canonical state snapshots (`procedure.state`, `report.status`), never guessing state from assistant speech transcripts.
3. **Accessibility & Responsive Design**:
   - The Cockpit dashboard must support desktop wide screens (grid layout) and tablet/mobile screens with zero horizontal overflow.
   - Colors must meet WCAG AA contrast standards, with high-visibility indicators for timer countdowns and emergency alerts.
