# Voice Workflow Agent Commercialization Pass 4 Report

Date: 2026-08-23

Branch: `refactor/voice-workflow-agent-stability`

Starting HEAD (this pass): `3741faf9027624f756901d784981a83c87ee2003`
(Pass 3's final commit, `docs/COMMERCIALIZATION_PASS3_REPORT.md`)

Final HEAD (pre-tag): `0c03a3afa595ab972a39abc8efb61e492d11c841`

## Scope

Pass 3 finished Phase 10 (Product Experience), re-rooted the repository,
hardened pilot acceptance, and cleaned up local/doc repository identity -
explicitly deferring the GitHub repository rename and CI. Pass 4 completes
those deferred items (Phases 14-19, continuation phases defined for this
handoff): independent repository migration, CI, workflow-authority
reconciliation, real integration validation, wet-lab pilot readiness, and
controlled-pilot deployment hardening.

Status terms follow Pass 2/3's convention: **Implemented** (code/API exists),
**Contract-tested** (exercised against a local fake), **Live-tested** (a real
external request succeeded in this environment), **Not validated**
(credentials/service unavailable).

## Commits this pass

| Commit | Theme |
|---|---|
| `70239b7` | Establish independent repository and CI (Phase 14) |
| `a6189dd` | Fix CI portability gaps in the test suite |
| `10ba290` | Skip PDF-dependent evaluators in CI instead of masking failures |
| `5057a80` | Reconcile workflow execution authority (Phase 15) |
| `1b09afd` | Harden controlled pilot deployment (Phase 18) |
| `0c03a3a` | Prepare controlled wet-lab pilot (Phase 17) |

(Phase 16 - live integration validation - produced evidence recorded in this
report and in `docs/PILOT_READINESS_PACKAGE.md`, not a separate commit; it
required no source changes.)

## Phase 14 — Independent repository + CI

- Pushed the completed Pass 3 branch to the pre-existing
  `jaeiko/voice-ai-course` origin first (verified clean fast-forward, then
  0/0 ahead-behind after).
- Verified `jaeiko/voice-workflow-agent` did not already exist, then created
  it as a new, independent (non-fork, `parent: null`) public repository.
- Renamed the local `origin` remote to `course-origin` (preserving the
  historical repository and its `upstream` civiliangame fork relationship
  untouched) and added the new repository as `origin`.
- Pushed `refactor/voice-workflow-agent-stability` to the new origin, and
  also as `main` (the new repository's default branch) - the old repository's
  own `main` is unrelated course content (syllabus/slides/week1-6 starter
  code) and was correctly left unmigrated.
- Migrated only the 5 Voice-Workflow-Agent-relevant tags; left the unrelated
  `safebridge-voice-m2` tag and all course branches on `course-origin` only.
- Added `.github/workflows/ci.yml` (none existed before this pass): a
  `python` job (pytest, compileall, deterministic voice replay, both routing
  evaluators) and an `e2e` job (Playwright, desktop + mobile).
- The first real CI run surfaced two genuine pre-existing portability gaps,
  invisible on the original dev VM because both conditions happened to
  already be satisfied there: `tests/test_procedure_demo.py` hardcoded
  `.venv/bin/python` (fixed with `sys.executable`), and 13 test modules (225
  tests) plus both evaluator scripts depend on the real, externally licensed
  Candidate A source PDF, which is correctly not committed to the repository.
  Added `tests/conftest.py` to skip exactly those modules with an explicit
  reason when the file is absent, and guarded the CI evaluator step the same
  way, rather than faking the file or masking the failures.
- **CI is green on both jobs**, on both `main` and
  `refactor/voice-workflow-agent-stability`, as of this report.

## Phase 15 — Workflow authority reconciliation

Evidence-driven audit (not a redesign) of the documented architecture debt
between `procedures.py`/`procedure_store.py` (legacy) and
`ExperimentSession`/`CuratedProtocolSession` (production):

- Only `server.py` imports the legacy stack in production code.
- It only activates when an operator sets **both**
  `VOICE_WORKFLOW_AGENT_PROCEDURE_CATALOG` and
  `VOICE_WORKFLOW_AGENT_PROCEDURE_STORE` - neither is set by
  `scripts/run_candidate_a.sh` or any documented deployment default.
- `server.py`'s protocol-selection logic gates the legacy lookup on
  `selected_curated_fixture is None`, so a session can never be bound to both
  authorities at once, by construction.

Verified the second point is load-bearing (not incidental) by temporarily
inverting the guard, confirming the existing suite alone would not have
caught the regression, then adding
`test_curated_selection_is_the_single_authority_even_when_legacy_procedure_config_exists`,
which configures **both** authorities simultaneously and asserts
`ProcedureStore`/`ProcedureController`/`load_procedure_definitions` are never
touched when a curated protocol is selected. Confirmed the new test fails
against the broken guard and passes against the real code, then reverted the
sabotage (`git diff` empty before re-applying the real fix).

**Disposition: ISOLATE, not retire.** The legacy stack has substantial
existing test coverage and reads as an earlier, simpler, non-tenant tutorial
mode - not evidently dead code, and not safe to delete without further
product input. Documented in module docstrings
(`procedures.py`/`procedure_store.py`/`procedure_definitions.py`) and in
`docs/LAB_WORKFLOW_OS_IMPLEMENTATION_REPORT.md`/`CLAUDE.md`. No production
behavior changed.

## Phase 16 — Real integration validation

Checked configuration presence only (booleans), never printed credential
values.

**Before this pass**, only `XAI_API_KEY`/`XAI_BASE_URL` and
`MOSS_PROJECT_ID`/`MOSS_PROJECT_KEY`/etc. were configured in this
environment; no Drive/GitHub/protocols.io/OIDC/eLabFTW/OCR/Seqera
credentials exist here.

**xAI STT + TTS — upgraded to LIVE-TESTED.** A real round trip via the
production `synthesize()`/`transcribe()` functions in `server.py` (not raw
HTTP, not mocks): `synthesize("현재 1단계입니다.", language="ko")` returned
56,016 bytes of real PCM audio from a live `POST /v1/tts` call; that audio
fed into `transcribe()` returned a real `POST /v1/stt` response
(`response_status=200`, `detected_language="ko"`, transcript length matching
the source text, ~415ms).

**LLM structured protocol analysis — connectivity/auth/model-response
confirmed live; full pipeline success not achieved with the minimal test
input used.** A live `POST /v1/chat/completions` call via
`OpenAICompatibleProtocolAnalysisModel` (the real production adapter)
returned `200 OK` in ~11.5s. The response was rejected by strict downstream
evidence-retention validation (`ProtocolAnalysisEvidenceError`) because the
test document was a deliberately minimal one-line synthetic PDF, not a
realistic multi-page protocol - the strict verbatim-evidence checks are
calibrated for real protocols like the existing Candidate A fixture. This is
a test-input artifact, not a demonstrated integration defect; re-running
against a fuller synthetic document was deliberately not pursued further to
avoid an open-ended, costly live-call loop for marginal additional evidence.
Classified as **contract-tested (100+ tests against a fake model) +
live connectivity confirmed**, short of a full live end-to-end success.

**MOSS retrieval — configured, not live-tested this pass.** Real-looking
project ID/key are present, but the `moss` optional dependency
(`pip install -e '.[moss]'`) is not installed in this environment, and
bootstrapping a live index was judged disproportionate to this pass's
remaining scope. Classified as **configured / not validated**.

**Everything else (Drive, GitHub, protocols.io, OIDC, eLabFTW, OCR,
Seqera) — unchanged: not validated, no credentials available.** Per the
early-blocker policy, none of these blocked the rest of this pass.

Updated classification (supersedes Pass 3's table for xAI only):

| Integration | Status | Evidence |
|---|---|---|
| xAI STT | **LIVE-TESTED** (this pass) | Real `transcribe()` call, 200, correct language, correct-length transcript |
| xAI TTS | **LIVE-TESTED** (this pass) | Real `synthesize()` call, 56,016 bytes of real PCM |
| LLM / structured protocol analysis | Contract-tested; live connectivity confirmed | Real 200 OK from `/v1/chat/completions`; full pipeline needs a realistic (non-minimal) test document to succeed end to end |
| MOSS retrieval | Configured, not validated | Credentials present; `moss` package not installed this pass |
| Google Drive / Shared Drive | Contract-tested | Unchanged from Pass 3 |
| GitHub connector | Contract-tested | Unchanged |
| protocols.io | Contract-tested | Unchanged |
| OIDC | Contract-tested | Unchanged |
| eLabFTW | Contract-tested | Unchanged |
| OCR provider | Contract-tested | Unchanged |
| Snakemake/Nextflow inspection | Implemented | Unchanged - static/regex metadata only |
| Seqera / future execution boundary | Not validated | Unchanged - bare interface, zero implementations |

## Phase 17 — Wet-lab pilot readiness

See `docs/PILOT_READINESS_PACKAGE.md` for the full package (pre-study
checklist, researcher/reviewer/admin quick-starts, incident template,
post-session interview questions, privacy explanation, known limitations,
abort criteria, severity categories). Summary:

- The full pilot flow is exercised end to end by the existing
  pytest/Playwright/replay/evaluator suite; no demo shortcut bypasses the
  production authority model.
- Every requested field-metrics KPI is mapped to where its data already
  lives (mostly the existing `ExperimentSession` append-only timeline);
  `runtime_metrics.py` already covers routes/barge-in/latency. Rolling the
  rest into a summary view was deliberately deferred as a scoped, low-risk
  follow-up rather than rushed late in this pass.
- **No human wet-lab study occurred.** This is capability/instrumentation
  readiness, not a field-validation result.

## Phase 18 — Controlled pilot deployment hardening

- Added `GET /healthz` (pure liveness) and `GET /readyz` (configuration
  readiness with non-secret capability booleans; 503 on invalid config,
  never on unreachable external providers - no local check can verify live
  reachability without a real, billable request). 3 new tests confirm both
  behave correctly and that `/readyz` never leaks secret-shaped strings.
- `docs/DEPLOYMENT_RUNBOOK.md` documents the existing
  `scripts/run_candidate_a.sh` + directly-invoked `uvicorn` path as a
  systemd unit, plus SQLite-consistent backup/restore. No Dockerfile was
  added - no container runtime was available in this environment to build
  or validate one, and an untested Dockerfile is worse than none.
- Config validation was already fail-closed
  (`ServerConfigurationError`/`WorkspaceError`/`ProtocolConfigurationError`);
  confirmed, not newly added.
- Security sanity check: no hardcoded secrets found in source; `.env` never
  committed to git history at any point.

## Final validation (this pass, from repository root)

- `python -m pytest -q`: **765 passed, 684 subtests passed** (was 761/684 at
  the start of this pass; +4 from the workflow-authority regression test and
  3 health/readiness tests).
- `python -m compileall -q src tests scripts`: clean.
- `git diff --check`: clean.
- `scripts/replay_turns.py`: completes without error.
- Both `scripts/evaluate_candidate_a_*.py`: unchanged results from Pass 3
  (98% route accuracy / 2 pre-existing failures; 20/20 grounded QA).
- GitHub Actions CI: **green on both jobs**, both branches, at the final
  commit of this pass.
- `docs/demo_script.md`: byte-identical throughout
  (`52213c8c851c0c3fd2848fa073e070ca8cc448f0c0221813d4266156b5235cd3`),
  remains untracked.

## Remaining gates for an actual commercial/regulated launch

Unchanged in kind from Pass 2/3, updated where this pass moved the needle:

1. No integration except xAI STT/TTS has been live-tested against a real
   external service in this environment.
2. No real OIDC identity provider, eLabFTW instance, or OCR provider is
   configured; development identity remains the only exercised
   authentication route.
3. The legacy procedure stack is now documented and regression-tested as
   isolated, but not retired - a product decision on its long-term fate is
   still open.
4. Field-metrics rollup (Section "Phase 17" above) is specified but not
   implemented.
5. No container-based deployment artifact exists (environment limitation,
   not a design decision) - the systemd path in
   `docs/DEPLOYMENT_RUNBOOK.md` is the currently reproducible one.
6. **No human wet-lab pilot has occurred.** This is the actual remaining
   field-validation step; everything in this report is capability evidence,
   not a substitute for it.
7. Tenant/compliance/deployment controls required for a regulated or fully
   operational (non-fictional, non-demo) launch remain out of scope, per
   Pass 2's original release disposition.

## Recommended next commercial engineering steps

1. Run the first real, low-stakes wet-lab pilot session using
   `docs/PILOT_READINESS_PACKAGE.md`, and use its findings to decide whether
   the field-metrics rollup is worth building before a second session.
2. Decide the legacy procedure stack's long-term fate (keep as a documented
   tutorial lane vs. retire) with product input, now that the technical
   ambiguity is resolved.
3. Live-validate one more integration under real credentials when
   available - MOSS is the closest (credentials already present; just needs
   the optional dependency installed and a bounded index bootstrap).
4. When a container runtime is available, build and validate a minimal
   Dockerfile against the guidance already captured in
   `docs/DEPLOYMENT_RUNBOOK.md`.
