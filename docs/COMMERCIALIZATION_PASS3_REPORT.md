# Voice Workflow Agent Commercialization Pass 3 Report

Date: 2026-08-23

Branch: `refactor/voice-workflow-agent-stability`

Starting HEAD: `eca3ecefa3eeb0cf1e730d6472cb60b79e8be4c7` (tag
`before-claude-phase10-20260823`)

Final HEAD (pre-push): `b17558a53e2ca1c392f7a2ded5cca2cccb7df0d0`

## Scope

Pass 2 (`docs/CODEX_COMMERCIALIZATION_PASS2_REPORT.md`) delivered Phases 1-9:
the tenant-aware protocol knowledge platform. Its own validation ledger
explicitly left **Phase 10 "Product Experience" pending on every axis**. Pass
3 finishes that unstarted phase, then performs three continuation phases
defined for this handoff (not part of the original numbered plan): repository
root promotion, pilot acceptance hardening, and repository-identity cleanup.

Status terms in this report follow Pass 2's convention:

- **Implemented**: production code and API/UI boundary exist.
- **Contract-tested**: the real adapter/contract is exercised against a local
  deterministic transport or provider fake.
- **Live-tested**: an actual external service or real browser/file path was
  used successfully in this environment.
- **Not validated**: credentials, service instance, or field evidence were
  unavailable; no live claim is made.

## Commits this pass

| Commit | Theme |
|---|---|
| `a0ceabb` | Polish researcher product experience and commercial demo (Phase 10) |
| `4246363` | Promote Voice Workflow Agent to repository root (Phase 11) |
| `79f009b` | Harden controlled pilot acceptance and validation (Phase 12) |
| `b17558a` | Finalize project repository identity (Phase 13, local/doc scope) |

## Phase 10 — Product Experience

- Extracted the inline `<style>` block to `static/app.css` (CSS only; the
  inline `<script>` block was deliberately left untouched, since
  `tests/test_frontend.py` re-executes it verbatim in a Node harness and
  splitting it would have broken that safety net for no real benefit).
- Added a color-coded experiment session-state badge
  (`#experiment-session-status-badge`) reflecting the server's actual
  `ExperimentSession.status` values (`ready`/`in_progress`/`paused`/`blocked`/
  `completed`/`stopped`) - not an invented state set.
- Tagged every rendered timeline event with `data-event-kind`
  (lifecycle/step/observation/evidence/reviewer-action/pause/resume/
  completion) and gave each an icon/color, without changing the event data
  itself.
- Gave the researcher's own OCR-acceptance control (`#protocol-ocr-accept`,
  `.btn-ocr-neutral`) a visually distinct, neutral style from the reviewer's
  actual approval action (`#reviewer-approve`, `.btn-reviewer-approve`, the
  only green "approve" affordance in the product) - the two already lived on
  separate workspace pages, but the button styling itself was previously
  identical.
- Added a mobile-breakpoint touch-target bump (44px+ minimum) for rail and
  workspace-action buttons, for gloved/bench-side usage.
- Added Playwright browser test infrastructure (`package.json`,
  `playwright.config.ts`, `tests/e2e/*.spec.ts`) - none existed before this
  pass. 28 tests across Desktop Chrome and Mobile Chrome cover the
  researcher, reviewer, and admin workspaces, empty/loading states, and
  responsive layout.
- Wrote `docs/DEMO_WALKTHROUGH_PHASE10.md`, mapping the user's own
  `docs/demo_script.md` outline to concrete existing UI elements. That file
  is new; `docs/demo_script.md` itself was never edited.
- **Real defect found via live browser testing, not assumed**: the standard
  demo launcher (`scripts/run_candidate_a.sh`) never set
  `VOICE_WORKFLOW_AGENT_WORKSPACE_ENABLED`, so every real run of the
  documented demo flow silently disabled the entire commercial workspace
  (reviewer, admin, experiment sessions, timeline) built in Phases 1-9. Fixed
  by enabling the existing flag in the launcher; confirmed via a live server
  run plus the full Playwright suite (28/28 passed) before and after.

## Phase 11 — Repository Root Promotion

The nested `voice-workflow-agent/` wrapper directory is retired; its
contents now live at the repository root via git-aware moves (history
preserved as renames). The four unrelated root-level course files
(`README.md`, `SYLLABUS.md`, `course-overview.html`, `slides.html`) were
archived byte-for-byte under `docs/course-archive/` rather than deleted,
since they predate this project and were never part of it. `.gitignore` is
the union of both prior files, plus `.claude/` (previously uncovered by any
ignore pattern).

Verified from the new root: editable install, the `voice-workflow-replay`
and `voice-workflow-evaluate` console scripts, the full pytest suite (761
passed, 684 subtests - matching the pre-migration baseline), `compileall`,
`git diff --check`, and a live server smoke test (index page, `/app.css`,
`/mic-capture-worklet.js`, `/api/workspace/session`, and the `/ws` WebSocket
path all responded correctly).

Rebuilding the venv from scratch surfaced a genuine, pre-existing gap:
`pytest` and `httpx` were never declared as dependencies anywhere - the old
venv had them installed ad hoc, invisibly. Added a `test` extra to
`pyproject.toml` (`pip install -e '.[test]'`) and documented it in
README/AGENTS.md/CLAUDE.md.

`docs/demo_script.md` sha256 (`52213c8c851c0c3fd2848fa073e070ca8cc448f0c0221813d4266156b5235cd3`)
was verified unchanged before the move, immediately after, and again at the
end of this pass. It remains untracked, exactly as required.

A new root `CLAUDE.md` was added as a short agent operating contract that
points to `AGENTS.md` for the code/product contract rather than duplicating
it.

## Phase 12 — Pilot Acceptance Hardening

Ran the established deterministic voice-routing evaluators against the
re-rooted repository:

- `evaluate_candidate_a_hardening.py`: 98% route accuracy, 0 unintended state
  mutations, 0 unsupported claims. 2 of the scored cases
  (`safety-general-followup`, `report-show`) fail. **Verified these are
  pre-existing**, not a Pass 3 regression, by checking out the exact
  pre-Phase-10 baseline commit (`eca3ece`) into a separate git worktree and
  re-running the identical script there - the same 2 cases fail at that
  baseline too.
- `evaluate_candidate_a_grounded_qa.py`: 20/20 cases, 1.0 accuracy across
  route, stop-intent, and whole-protocol-scope checks, 0 unintended
  mutations.

### External integration classification

No integration below has in-repo evidence of a successful **live** external
call during this pass:

| Integration | Status |
|---|---|
| xAI STT | Implemented (real client wired; exercised only via manual, opt-in diagnostics) |
| xAI TTS | Implemented (same as above) |
| LLM / structured protocol analysis | Contract-tested (`FakeModel`/`FakeChunkModel`/`FakeClient`) |
| Google Drive / Shared Drive | Contract-tested (`FakeTransport`) |
| GitHub connector | Contract-tested (`FakeTransport`) |
| protocols.io | Contract-tested (`FakeTransport`) |
| OIDC | Contract-tested (fake JWKS; no real IdP) |
| eLabFTW | Contract-tested (`FakeElnTransport`) |
| OCR provider | Contract-tested (`FakeOcrProvider`); no bundled real implementation exists |
| Snakemake/Nextflow inspection | Implemented (static/regex metadata only; deliberately no execution path) |
| Seqera / future execution boundary | Not validated (bare interface, zero implementations, zero tests) |

### Known limitation carried forward (not fixed this pass)

A second, older workflow-state-machine (`procedures.py` /
`procedure_store.py`, with its own schema and tool set) runs alongside the
production `ExperimentSession`/`CuratedProtocolSession` authority. It
predates Pass 3 and is reachable in production code, not dead. Its
disposition (an intentional demo/tutorial lane vs. a retirement candidate)
was deliberately left unresolved - reconciling two workflow authorities is a
Phase 1-9 architecture question, not a product-polish or hardening task, and
touching it without a dedicated review risks the exact "duplicate workflow
authority" failure mode this project's own invariants forbid.

## Phase 13 — Repository Identity (local/doc scope)

`origin` is `jaeiko/voice-ai-course`, forked from `upstream`
`civiliangame/voice-ai-course` - evidence this may still be active course
infrastructure rather than an independent repository. **The GitHub
repository rename and remote URL change were explicitly deferred to the
repository owner's separate decision** rather than performed automatically,
per that evidence and the owner's explicit choice this session. `gh auth
status` confirmed rename permission would have been technically available;
it was not exercised.

Completed within local/doc scope:

- Fixed the one live (non-historical) stale path reference:
  `docs/CANDIDATE_A_REAL_VOICE_ACCEPTANCE.md`'s acceptance checklist gave an
  exact `cd` command into the now-retired nested directory.
- Cleaned 25 total "SOP used as a loose synonym for protocol" instances
  across `README.md`, `docs/LAB_WORKFLOW_OS_IMPLEMENTATION_REPORT.md`,
  `tools.py`, `.agent/product_improvement_strategy.md`,
  `docs/LAB_ADMIN_PRODUCT_PLAN.md`, `docs/PRODUCT_EVOLUTION_PLAN.md`, and
  `docs/PRODUCT_IMPROVEMENT_PROPOSAL.md`. Left alone: the distinct,
  org-governed facility-safety-document corpus (`safety_pack.py`, MOSS
  retrieval, `APPROVED_DOCUMENT_OPERATIONS.md`), two genuine real-world
  regulatory-domain statements (GLP/GMP/ISO 17025 context, not this system's
  own data model), eight dated/historical-banner audit docs (left as
  historical record per `AGENTS.md`'s own documentation-authority rule), and
  cosmetic test-fixture literals like `"SOP-1"` (low-risk, no behavior or
  documentation impact).

## Final validation (this pass, from repository root)

- `python -m pytest -q`: **761 passed, 684 subtests passed**.
- `python -m compileall -q src tests scripts`: clean.
- `git diff --check`: clean.
- `scripts/replay_turns.py`: completes without error; read-only turns
  correctly report `state_mutation: false`.
- `evaluate_candidate_a_hardening.py` / `evaluate_candidate_a_grounded_qa.py`:
  see Phase 12 above.
- Playwright (`npx playwright test`): **28 passed** across Desktop Chrome and
  Mobile Chrome.
- Live server smoke test: index, static assets, `/api/workspace/session`,
  and `/ws` all responded correctly from the new repository root.
- `docs/demo_script.md`: byte-identical throughout
  (`52213c8c851c0c3fd2848fa073e070ca8cc448f0c0221813d4266156b5235cd3`),
  remains untracked.

## Remaining pilot release gates

Unchanged from Pass 2's disposition and not addressed by Pass 3, since none
of it was in scope for a product-polish/re-rooting/hardening pass:

1. No integration has been live-tested against a real external service in
   this environment - only contract tests exist.
2. No real OIDC identity provider, eLabFTW instance, or OCR provider is
   configured; the development identity path remains the only exercised
   authentication route.
3. The legacy `procedures.py`/`procedure_store.py` workflow stack's
   relationship to the production authority is unresolved.
4. Tenant/compliance/deployment controls required for a regulated or
   operational (non-fictional, non-demo) launch remain out of scope, per
   Pass 2's own release disposition.
5. The GitHub repository identity (`jaeiko/voice-ai-course`) has not been
   migrated; see Phase 13 above.

## Recommended next commercial engineering steps

1. Live-validate one integration end to end (most likely xAI STT/TTS, since
   the real client is already wired) under real credentials in a sandboxed
   environment, then update this classification honestly.
2. Schedule a dedicated review of the legacy procedure stack: either scope
   and document it explicitly as a separate demo lane, or plan its
   retirement - do not let it accumulate further undocumented drift.
3. Once the course relationship with `civiliangame/voice-ai-course` is fully
   wound down, perform the GitHub repository rename
   (`jaeiko/voice-ai-course` → `jaeiko/voice-workflow-agent`) and the local
   directory rename together, as one deliberate administrative action.
4. Wire Playwright and pytest into a CI workflow - neither runs
   automatically today; both are currently manual, documented commands.
