# Product and engineering roadmap

Revised 2026-10-10 (lane CL) to the human decisions of that day, and
2026-10-14 (lane DOC3) to what has been built since. VoineyLab is an MVP
prototype, not field-validated ("MVP 시제품 — 현장 미검증"). Everything under
"Next lanes", "Before a pilot" and "Later" is planned, not built, unless it
says otherwise.

## Built since 2026-10-10 (each dated by its lane's merge)

- **PL, 2026-10-13** — the pilot starts without an approved safety catalog as
  `source_protocol_only` (human decision "B안"); a catalog that is there is
  checked; an unset model role no longer falls back to xAI; a data deletion
  tool; the HTTPS set-up guide (written, not run).
- **SP1, 2026-10-11** — voice level setting, noisy mode, ignored-speech list
  (synthetic tests only).
- **RN, 2026-10-12** — "다시 분석"; the retry button's no-reaction cause.
- **UI0 / UI2 / UI3, 2026-10-12–14** — the screen's before / during / after
  views, the protocol list, the start check and source comparison, the
  after-review, mobile layout and "add to home screen"; lane UI3 added the
  upload of Word and photos, the reagent-table box, the `source_protocol_only`
  notice on the start card and the safety box, and lane RA's fields on the
  run view.
- **AN, 2026-10-13** — a long document read in pieces (up to 400 pages); Word,
  photo (HEIC optional) and reagent-table sources (server side); the
  remaining evidence-check failures (EV4).
- **RA, 2026-10-14** — the router-first switch `VOINEY_LAB_ROUTER_FIRST`,
  built and **off**.

## Next lanes

- **RV — review tab (in progress).** Upload → automatic analysis (about one minute) →
  review (by the uploader or anyone; the reviewer's name is required to
  register) → "검토 완료하고 등록" → the reviewed version saved (reused,
  recorded as v1/v2) → start check → voice guidance. Values changed in review
  are shown as "검토자 입력", values the AI proposes as "AI 제안"; a person
  confirms either before it becomes a run value. Values and safety come only
  from the source or a person's confirmation; an unfriendly term may get a
  labelled general explanation; the AI never writes safety precautions or
  completion criteria. This replaces the PI/administrator approval, which is
  gone (2026-10-08) and does not come back.
- **Filtering other people's speech, step 2** — telling speakers apart; step 1
  (loudness) is built.
- **Mobile web on a real phone** over HTTPS (`docs/PILOT_HTTPS_SETUP.md`); a
  native app later.
- **Understanding led by the LLM router** — decide whether to turn
  `VOINEY_LAB_ROUTER_FIRST` on, after a person's voice test; the front rules
  stay, within the one routing line of AGENTS rule 3.
- **Long documents** — the three documents over 60 pages measured on
  2026-10-13 all ended as "일부 조각 분석 실패"; waiting before a piece's
  retry or lowering the number of pieces in flight (HTTP 429 under load) is
  not done.
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
  says "설정 필요", and the code no longer falls back to xai, so an unset role
  is refused by name).
- The external-AI notice text (`docs/PILOT_GUIDE.md` §4) settled by ethics and
  legal review, the HTTPS domain decided and set up, and each provider's
  retention terms checked.
- The Word report's opening sentence for `source_protocol_only` runs
  (`institution_safety_notice` in the record is not yet written into it).

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
