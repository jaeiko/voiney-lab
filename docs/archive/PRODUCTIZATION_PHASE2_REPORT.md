# Productization Phase 2 — Reviewer Experience

Date: 2026-08-24  
Status: Complete  
Product disposition: Controlled Pilot Ready, Field-Unvalidated

## Outcome

The reviewer workspace is now an impact-first decision surface instead of a raw
revision diff. Before acting, a reviewer can identify the protocol, exact
version, requester, immutable request reason, structured change summary,
experimental-impact evidence, risk evidence, source, and prior decisions.

Approval state remains server-owned and append-only. The browser only offers
actions returned by the review packet, requires an explicit rationale, and
stages the consequence for a second confirmation before submitting it.

## Implemented changes

### Review queue and identity

- Enriched each pending and resolved inbox item with protocol title, exact
  revision/version, requester display identity, immutable request reason, source
  status, and an explicit risk-assessment state.
- Separated pending work from handled requests so a completed item does not look
  actionable.
- Kept stable principal identifiers available for audit while presenting the
  tenant-safe display name as the primary reviewer identity.

### Impact-first review packet

- Added a deterministic review projection containing review context, structured
  change summary, experimental impact, risk, allowed decisions, and history.
- Summarized changed top-level fields, step and warning counts, and exact
  structured-adaptation changes without asking the browser or model to infer
  scientific consequences.
- Rendered missing experimental-impact and risk review as `not_assessed` rather
  than inventing a level. A source-provided risk signal is displayed separately
  and is never promoted to an approved risk assessment.
- Moved the line-level technical diff into a collapsed detail panel so it remains
  available without dominating the decision flow.

### Decision safety and consequences

- Limited transitions to `review_required` → `approved` or `rejected`, and
  `approved` → `revoked`. Rejected and revoked revisions are terminal.
- Added stale-decision and duplicate-key fences at the durable transaction
  boundary. A replay of the same idempotency key is identified as a replay;
  another attempt against resolved state returns a conflict.
- Resolved the matching source-inbox request in the same transaction as the
  append-only approval event.
- Required a non-empty reviewer rationale and an explicit inline confirmation
  that states the consequence of approval, revision request, or future-use
  revocation.
- Preserved current sessions when future use is revoked; the UI does not imply
  that revocation silently mutates an active experiment.

### Audit history

- Added actor display identity, role, rationale, timestamp, decision, and exact
  affected version to the review history.
- History and decision availability come from the durable ledger and current
  revision state, not from client-side reconstruction or assistant prose.

## Architecture and authority impact

The reviewer projection extends the existing `WorkspaceStore` and workspace API;
it does not introduce a second classifier, LLM mutation route, or client-owned
workflow checkpoint.

- Source-inbox enrichment is a read-only join over tenant-scoped durable records.
- The review packet is computed from immutable revision content, source metadata,
  and append-only approval history.
- Approval/rejection/revocation remains a parameterized-SQL transaction with
  identity, tenant, state-transition, and idempotency checks.
- Frontend strings continue to be inserted with `textContent` or the existing
  safe row renderer.

## Product evaluation

- Reviewer usability: improved. Reviewers start with experimental context and
  consequences, while the raw diff remains available on demand.
- Product trust: improved. Unknown impact and risk are plainly unknown; request
  reasons and actor identity are not inferred or overwritten.
- Operational risk: reduced. Only legal transitions are exposed and accepted,
  stale submissions fail closed, and destructive future-use revocation requires
  an explicit second confirmation.
- Laboratory adoption alignment: improved, but not field-validated. The packet
  is suitable for a controlled pilot review exercise while preserving source and
  version traceability.

## Verification evidence

- Focused identity, workspace, API, adaptation, connector, dry-run, and frontend
  suite: 61 tests passed.
- Full Python suite: 783 tests and 691 subtests passed.
- Playwright acceptance matrix: 34 tests passed across Desktop Chrome and Mobile
  Chrome, including impact-first review, technical-detail disclosure, decision
  confirmation, source panels, empty/loading states, and all three roles.
- `python scripts/replay_turns.py`: passed; all seven replay turns stayed on the
  curated router and no replay turn mutated workflow state.
- `python -m compileall -q src tests scripts`: passed.
- `git diff --check`: passed.

## Remaining risks and deferred work

- Experimental impact and reviewed risk intentionally remain `not_assessed`
  because no approved immutable assessment schema exists. Adding one requires a
  named authority, source linkage, lifecycle, and regression coverage.
- “Request revision” currently records a durable rejection decision and resolves
  the inbox item. It does not send an external notification or create an
  assignee-managed work item, so the UI does not claim that it does.
- The inline confirmation is covered by responsive browser tests but still needs
  the pending screenshot-based accessibility, focus-order, and wet-lab review.
- Approval analytics are operational telemetry, not decision authority. A future
  hardening pass should explicitly define retry behavior if telemetry recording
  fails after the durable decision transaction commits.

Phase 2 satisfies its exit gate without assigning scientific authority to the
model or browser. The next sequential phase is administrator experience
productization.
