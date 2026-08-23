# Product and engineering roadmap

## P0 — controlled pilot readiness

- Finish actual-browser upload/review/session/error recovery checks in CI.
- Add OIDC/SSO, tenant-scoped RBAC, admin roles, CSRF policy, rate limits, and
  centralized secret management; replace the shared admin token.
- Define encryption, backup, retention, deletion, incident response, DPA, and
  provider data-handling policy.
- Add one production-grade ELN write-back connector with idempotency and human
  review; keep the app out of the system-of-record role.
- Run noisy-lab/accent evaluations with real target users and publish correction,
  task-completion, interruption, and latency distributions.
- Establish protocol approval roles, revision diff/revocation, and audit export.

## P1 — repeatable commercial product

- Protocol review workspace with assigned reviewers, inline source diffs,
  clarification resolution, and approval signatures.
- Durable monitoring/export for route, tool, latency, onboarding funnel, provider
  spend, correction, abandonment, and blocked-step metrics.
- ELN/LIMS/instrument connector SDK and customer-specific data maps.
- Organization/tenant isolation, regional deployment, SCIM, audit API, and support
  tooling.
- Offline/degraded mode and lab-device/headset qualification.
- Accessibility/usability validation for gloves, PPE, mobility, vision, and noisy
  environments.

## P2 — advanced workflows

- Reviewed execution semantics for conditionals, repeats, parallel work, reusable
  subprocedures, multi-day continuation, and cross-shift handoff.
- Human-approved anomaly clustering and protocol improvement suggestions.
- Multilingual terminology packs evaluated per language and facility.
- Richer source-linked diagrams and equipment imagery with rights provenance.

## Explicit non-goals until separately validated

- autonomous protocol approval or modification;
- autonomous safety decisions or work-resume authorization;
- clinical or medical decision support;
- unsupervised equipment control;
- claims of 21 CFR Part 11, GLP/GMP, ISO, or GxP compliance from architecture or
  unit tests alone.
