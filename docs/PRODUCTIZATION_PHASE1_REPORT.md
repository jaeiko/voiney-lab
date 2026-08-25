# Productization Phase 1 — Researcher Experience

Date: 2026-08-24  
Status: Complete  
Product disposition: Controlled Pilot Ready, Field-Unvalidated

## Outcome

The primary bench view now answers the five questions required by the
productization goal before a researcher starts or resumes Voice:

1. the selected protocol/experiment;
2. the exact approved or development revision;
3. the recorded approval responsibility, or an explicit statement that final
   approval is absent;
4. the current durable step and experiment state;
5. the actions currently available.

Internal state remains canonical and server-owned. The browser translates voice
states into researcher-facing labels, but it does not derive workflow progress,
approval, completion, or recovery from assistant prose.

## Implemented changes

### Start and protocol context

- Added a primary experiment context card above setup controls.
- Renamed ambiguous “experiment PDF” language to protocol selection/registration
  language.
- The start action is disabled until the catalog confirms an executable protocol
  or the server exposes an eligible durable experiment to resume.
- Added a read-only catalog approval projection sourced from the append-only
  approval/development event: status, final-approval flag, actor, role, timestamp,
  and authority. Missing actor evidence remains missing rather than inferred.
- A development fixture is clearly labelled as not finally approved.
- A recovered historical revision is never assigned the current catalog
  revision's approval identity; the UI asks for approval-record re-verification.

### Voice interaction

- Mapped internal states to product labels while retaining their canonical
  values internally: Ready, Connecting, Listening, Understanding request,
  Providing guidance, and Check required (rendered in the Korean product UI).
- Existing deterministic completion, ambiguity, revision, identity, safety,
  timer, and confirmation gates were not changed.
- Recoverable voice and timeline errors continue to state that the researcher
  must not assume workflow state changed.

### Resume and new-experiment behavior

- Added a deterministic recovery projection to the experiment timeline:
  eligibility, latest recovery event, exact restored protocol/revision/current
  step/completed-step count, deliberately non-restored state, and next action.
- The disclosure explicitly says that pending confirmations, prior conversation,
  and active timers are not restored.
- An explicit new-session action clears the browser's durable-session selection
  after the server stop boundary. It does not complete or advance a step.
- Closed/completed experiments offer a new experiment, not a resume action.

### Responsive and acceptance coverage

- Added responsive styling for the context card and one-column narrow layouts.
- Strengthened browser acceptance to assert loaded content rather than empty DOM
  shells and replaced an invalid `networkidle` assumption for a live WebSocket
  product with an explicit production-renderer readiness condition.

## Architecture and authority impact

The change adds two read-only projections and presentation logic. It does not add
an intent classifier, mutation path, provider authority, or client-side workflow
checkpoint.

- `ProtocolCatalog.approval_context` reads the same append-only event already
  used to determine execution availability.
- `WorkspaceStore.experiment_timeline` returns a recovery explanation computed
  from the durable session and event ledger without writing either.
- `server.py` exposes approval context through the existing catalog endpoints.
- The browser renders these server facts with `textContent` and keeps internal
  voice-state values available for generation/staleness fences.

## Product evaluation

- Researcher usability: improved. Start, resume, and closed-session states have
  distinct calls to action and a first-time user can find identity, version,
  approval, current step, and actions in one place.
- Product trust: improved. Approval responsibility and the development-only
  boundary are visible, and recovery no longer implies that conversation,
  confirmations, or timers survived.
- Operational risk: reduced. Start fails closed without executable catalog
  evidence, historical revision mismatches are called out, and the browser does
  not silently reuse a selected experiment when “new session” is chosen.
- Laboratory adoption alignment: improved, but not field-validated. The flow is
  easier to explain in a pilot and preserves the exact authority model required
  for traceability.

## Verification evidence

- Focused API, catalog, experiment, frontend, and WebSocket integration suite:
  72 tests and 10 subtests passed.
- Full Python suite: 782 tests and 691 subtests passed.
- Playwright acceptance matrix: 32 tests passed across Desktop Chrome and Mobile
  Chrome, covering researcher, reviewer, admin, empty/loading, and responsive
  flows.
- `python scripts/replay_turns.py`: passed; all replay turns stayed on the
  curated router and read-only requests did not mutate state.
- `python -m compileall -q src tests scripts`: passed.
- `git diff --check`: passed.

## Remaining risks and deferred work

- The approval actor is currently a stable principal identifier plus role because
  the public catalog does not have a display-name projection. Phase 2 should add
  a tenant-safe reviewer display identity to reviewer/audit views.
- Refresh recovery remains an explicit session selection when more than one
  durable experiment exists; this avoids guessing but should be field-tested.
- Active timers deliberately do not resume. A future durable-timer design would
  require server-owned semantics and safety review, not a browser timer restore.
- Automated responsive acceptance passed, but the Phase 0 screenshot-based UX
  and accessibility audit remains pending; no claim of completed visual/WCAG or
  wet-lab validation is made.

Phase 1 satisfies its exit gate without weakening the completion workflow. The
next sequential phase is reviewer experience productization.
