# Product and engineering roadmap

Revised 2026-10-10 (lane CL) to the human decisions of that day. VoineyLab is
an MVP prototype, not field-validated. Everything below is planned, not built,
unless it says otherwise.

## Next lanes

- **RV — review tab.** Upload → automatic analysis (about one minute) →
  review (by the uploader or anyone; the reviewer's name is required to
  register) → "검토 완료하고 등록" → the reviewed version saved (reused,
  recorded as v1/v2) → start check → voice guidance. Values changed in review
  are shown as "검토자 입력", values the AI proposes as "AI 제안"; a person
  confirms either before it becomes a run value. Values and safety come only
  from the source or a person's confirmation; an unfriendly term may get a
  labelled general explanation; the AI never writes safety precautions or
  completion criteria. This replaces the PI/administrator approval, which is
  gone (2026-10-08) and does not come back.
- **SP1 — filtering other people's speech** (in progress).
- **Mobile web (PWA)** for the experimenter's screen; a native app later.
- **Understanding led by the LLM router** behind the deterministic front
  rules, within the one routing line of AGENTS rule 3.
- **RA** — bring back the Answer, Source and Visual roles of `multi_brain.py`.
- **PL** — decide the approved safety catalog and `scripts/run_pilot.sh`
  (today the pilot launcher refuses to start without that catalog).
- **Later** — Moss again, for searching past records and manuals (its
  approved-catalog reranker was deleted on 2026-10-10, lane CL).

## Before a pilot

- Actual-browser upload/review/session/error recovery checks in CI.
- OIDC/SSO login, CSRF policy, rate limits, and centralized secret management;
  replace the shared admin token.
- Encryption, backup, retention, deletion, incident response, DPA, and each
  provider's data-handling terms.
- Noisy-lab/accent evaluations with real target users
  (`docs/VOICE_FIELD_EVALUATION_PLAN.md`): correction, task-completion,
  interruption, and latency distributions.
- Every model role's provider and model set explicitly (the setting table
  says "설정 필요"; the code's old xai fallback is still to be removed).

## Later

- Durable monitoring/export for route, tool, latency, onboarding funnel, provider
  spend, correction, abandonment, and blocked-step metrics.
- Offline/degraded mode and lab-device/headset qualification.
- Accessibility/usability validation for gloves, PPE, mobility, vision, and noisy
  environments.
- Reviewed execution semantics for conditionals, repeats, parallel work, reusable
  subprocedures, multi-day continuation, and cross-shift handoff.
- Multilingual terminology packs evaluated per language and facility.
- Whether to write records to an ELN or LIMS is not decided; the product does
  not replace either.

## Removed and not planned

- PI or administrator approval, reviewer and administrator screens, revision
  approval, rejection and revocation, lab adaptations (2026-10-08, lane DI).
- The eLabFTW export, the protocols.io / Drive / GitHub imports and the
  dry-lab metadata lane (2026-10-08, lane DI).
- The voice safety report and its hand-off, the approved-safety-manual and
  lab-reference searches, and the Moss reranker (2026-10-10, lane CL).

## Explicit non-goals until separately validated

- autonomous protocol approval or modification;
- autonomous safety decisions or work-resume authorization;
- clinical or medical decision support;
- unsupervised equipment control;
- replacing an ELN or LIMS;
- claims of 21 CFR Part 11, GLP/GMP, ISO, or GxP compliance from architecture or
  unit tests alone.
