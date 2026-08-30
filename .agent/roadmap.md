# Product and engineering roadmap

## P0 — controlled pilot readiness

- Run the first supervised, one-lab/one-low-risk-protocol field pilot against the
  current actual-browser approval, execution, degraded-voice, and recovery gates.
- Live-validate operational OIDC/SSO, the selected provider accounts, and the
  deployment secret manager; development identity remains prohibited in
  operational scope.
- Approve deployment-specific encryption, durable-record deletion/legal-hold,
  backup retention, incident response, DPA, and provider data-handling policy.
- Live-validate the eLabFTW connector or use the existing reviewed CSV/export
  fallback; keep the app out of the system-of-record role.
- Run noisy-lab/accent evaluations with real target users and publish correction,
  task-completion, interruption, and latency distributions.
- Perform a real stopped-process backup/restore drill on the pilot deployment.

## P1 — repeatable commercial product

- Add assigned reviewers/approval signatures to the current diff, clarification,
  immutable decision, and withdrawal workspace.
- Extend the current tenant KPI/event metrics with persistent multi-process
  timing, onboarding funnel, provider spend, abandonment, and support export.
- ELN/LIMS/instrument connector SDK and customer-specific data maps.
- Multi-lab membership selection, regional deployment, SCIM, audit API, and
  privacy-safe support tooling on top of current single-workspace tenant RBAC.
- Guided Lab Adaptation authoring, lab-owned terminology packs, and
  lab-device/headset qualification.
- Accessibility/usability field validation for gloves, PPE, mobility, vision,
  and noisy environments; the current software P0 controls are not certification.

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
