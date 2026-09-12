# CLAUDE.md — agent operating contract

This file governs how an agent works in this repository. `AGENTS.md` governs
what the code and product are allowed to do — read it first; it is the
authoritative engineering contract and is not duplicated here.

## Project

Voice Workflow Agent is a voice-first laboratory protocol knowledge and
execution layer (controlled-pilot system, not a validated GLP/GMP/clinical
system). This repository root **is** the project — there is no nested
`voice-workflow-agent/` wrapper directory; develop directly from here.

Authoritative docs, in order of precedence: `README.md` (current runnable
contract) → `AGENTS.md` and `.agent/*.md` (contributor design constraints),
where `.agent/architecture.md` is the primary source for the current
architecture → `docs/CURRENT_ARCHITECTURE.md` (the docs-side component,
state-authority, persistence and failure view) → `docs/MIGRATION_NOTES.md`
(schema history).

`docs/ARCHITECTURE_MAP.md` is **not** in that list: its own header records it
as a snapshot taken before the Laboratory Workflow OS implementation. Read it
for history, and where it and `.agent/architecture.md` disagree,
`.agent/architecture.md` is the one that describes the code as it stands.

The list is also shorter than it used to be. The documents that held the
"handoff of record" and "phase evidence" roles were superseded and now live in
`docs/archive/`; nothing under `docs/` has taken those roles over, so the list
stops rather than pointing at a replacement that does not exist.

Older phase-numbered or `CODEX_*`-prefixed documents under `docs/` are
historical evidence; they cannot override the documents above.
`docs/course-archive/` holds the original course materials this repository
grew out of — leave them as historical record, not something to build against.

## Mandatory preflight

Before making changes in a new session, run and read the output:

```bash
pwd
git branch --show-current
git status --short
git log --oneline --decorate -10
```

Investigate any unfamiliar untracked file or directory before touching it —
it may be in-progress work. `docs/demo_script.md` is a known case: it is
**user-owned, intentionally untracked, and must never be edited, committed,
or overwritten.** If you need to write about the demo flow, create a new file
(e.g. `docs/DEMO_WALKTHROUGH_*.md`) instead.

## One worker per working tree

Do not run two agents against this same working tree concurrently. Use a
separate git worktree (or a separate clone) for parallel work so uncommitted
changes and running dev servers don't collide.

## Server-owned workflow authority

The server is the only workflow authority. Model/voice output never advances
a protocol step, marks completion, approves a protocol revision, converts an
observation into an instruction, resumes a blocked experiment, or writes to
an ELN without explicit user confirmation. Do not add a second router, a
duplicate workflow state machine, another `ExperimentSession` authority, a
parallel approval subsystem, or a parallel protocol store — see `AGENTS.md`
rule 2–3 for the full boundary. (A pre-existing, older `procedures.py` /
`procedure_store.py` workflow stack runs alongside the production
`ExperimentSession` / `CuratedProtocolSession` stack. It is explicitly
config-gated off by default — the module docstring at the top of
`src/voice_workflow_agent/procedures.py` states the two environment variables
that must both be set and why the commercial launcher sets neither. Mutual
exclusivity is regression-tested by
`test_curated_selection_is_the_single_authority_even_when_legacy_procedure_config_exists`
in `tests/test_curated_protocol_cascade.py`, which configures *both*
authorities and proves the legacy one is never constructed. Treat it as an
isolated, documented lane, not something to silently extend, merge with the
production authority, or duplicate further.)

## Terminology: Protocol vs. SOP

Default to "Source Protocol", "Protocol", "Protocol Revision", "Lab-adapted
Protocol", or "Approved Protocol Revision". Only use "SOP" / "Standard
Operating Procedure" for the distinct, org-governed facility-safety-document
corpus (`safety_pack.py`, the MOSS/approved-document retrieval path) — never
as a casual synonym for a protocol or protocol revision. Approving a protocol
revision does not make it an SOP.

## Testing expectations

```bash
python -m venv .venv && source .venv/bin/activate && pip install -e '.[test]'
python scripts/replay_turns.py
VOICE_WORKFLOW_AGENT_MOSS_ENABLED=false \
VOICE_WORKFLOW_AGENT_WORKSPACE_ENABLED=false \
VOICE_WORKFLOW_AGENT_EXPERIMENT_REPORTS_ENABLED=false \
python -m pytest -q
python -m compileall -q src tests scripts
git diff --check
```

The three flags match the README's Verification section: `server.py` calls
`load_dotenv`, so a repo-root `.env` otherwise changes the result silently.
When you report a pass count, say which of the README's two baselines it is —
condition A (externally licensed source PDFs present) or condition B (absent).
The number alone does not identify the run.

Browser acceptance tests (`tests/e2e/`, Playwright) run separately —
`npm install && npx playwright install --with-deps chromium && npx playwright
test` — see the README's Verification section. Provider calls must stay
fake-backed in tests; do not add tests that require live credentials.

## No fake external validation

Never describe protocols.io, Google Drive, GitHub, OIDC, eLabFTW, the OCR
provider, or similar integrations as "live-tested" unless a real external
request actually succeeded in this environment during this work. Contract
tests against a fake transport are "contract-tested," not "live-tested" —
keep that distinction explicit in code comments, docs, and reports.

## No destructive shortcuts

Do not use `git reset --hard`, `git clean -fd`, force-push, or similar
destructive/irreversible git operations to resolve a problem unless the user
explicitly asks for that specific action. Investigate unfamiliar state
instead of discarding it.

## Branch workflow

Work happens on feature/refactor branches off `main`; do not push directly
to `main`. Commit each phase of multi-phase work separately with a
descriptive message, and confirm with the user before pushing to a shared
remote branch or renaming/transferring the GitHub repository itself.

## Historical archive

`docs/archive/` holds superseded phase reports. They are historical record
only. Do not read them, cite them, or base decisions on them unless the user
explicitly asks about a specific past decision.