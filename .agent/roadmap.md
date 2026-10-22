# Roadmap — now, next, later

Rewritten 2026-10-20 (lane DOC4). "Now" is built and described in `docs/FEATURES.md`;
everything under "Next" and "Later" is **not built** unless it says so.
Dates and lane names of past decisions are in `docs/DECISIONS.md`.

How to rank work: reliability, state integrity, safety gates and grounded
answers first; then bench smoothness (latency, recovery, exports); then
richer workflows and integrations.

## Now (built)

- Bringing a protocol in: PDF, Word, photo, reagent table; public Google
  Drive/Docs/Sheets links (server route only).
- OCR with two engines compared place by place; long documents in pieces up
  to 400 pages; automatic retries; reanalysis; Korean translation.
- Source-evidence check with requote; confirmation cards; review versions;
  safety statements never rewritten.
- Start wizard with the two-question start check and the safety statements;
  facility safety catalog optional (`source_protocol_only`).
- Voice loop: calibration and loudness filter, noisy-place mode, noise
  endpoint, emergency gate, front rules, LLM router (off) and router-first
  switch (off).
- Steps, completion, timers with notices, "그거" re-asks, outside-PDF
  explanation, source figures, drawn diagrams and web explanations (off by
  default).
- Records, notebook questions, the lab-notebook report and exports (Word,
  Markdown, JSON, CSV on screen; PDF, lab-meeting summary, xlsx on the
  server), the PI "확인함".
- Accounts with invites, the five-tab screen, connection-loss recovery,
  pilot metrics and rating, account and data deletion, backups.
- A store-app shell (Android, debug build only).

## In progress (other lanes, 2026-10-20)

- Screen structure, steps 2 and 3 (sheets and tabs).
- Analysis finishing work.
- Further notebook, starter-pack and Drive-link work, and a quality sweep.

Their documentation lands with stage 2 of the documentation rewrite.

## Next — waiting on a person's decision or a short lane

| Item | State now | Decision or work needed |
|---|---|---|
| Make review mandatory for some analyses | Optional; a fully passed analysis has no cards | Whether OCR pages, pieced documents and repaired answers must be reviewed |
| Turn the router (and router-first) on | Built, off | A person's voice test first |
| Telling speakers apart | Loudness only; speaker verification not built | Build or not; legal review of training data and biometric consent; real recordings |
| End-of-speech silence | 1000 ms | Re-measure on real recordings before changing |
| Screens for server-only features | Drive link import, PDF / lab-meeting / xlsx exports, "확인함", past reports after reload have no screen | Screen work |
| Store app connected to the screen | `native-bridge.js` not loaded; bundled build cannot reach a server | Wire it; test on a real phone; store registration |
| Mobile web over HTTPS | Guide and script written, not run | Choose a domain, run it, test on a phone |
| Plain-words rewrite of screen and voice text | Rules and glossary written; strings not changed | Stage 2 go-ahead |
| Broken diagnostic scripts | Ten scripts in `scripts/` fail (deleted modules) | Fix or delete |
| Start check enforced by the server | Wizard order only | Whether the server should refuse a start without it |
| Missed repeats and values | Reason codes kept, no detector on the live path | Whether to rebuild a check |
| Long documents | Over 60 pages measured as failing (2026-10-13), since then failed pieces become items | Re-measure |

## Before a pilot

- Every model role, STT and TTS provider set explicitly in `.env`.
- The external-AI notice text settled with ethics/legal review, and each
  provider's data-retention terms checked (`docs/PILOT_GUIDE.md`).
- HTTPS domain chosen and set up; invite links made for the participants;
  the admin token or an admin account for the metrics screen.
- Backups scheduled; the deletion procedure rehearsed.

## Later

- Field evaluation in a working lab with consenting participants: noise,
  accents, gloves; correction, completion, interruption and latency
  distributions (`.agent/testing_strategy.md`, "Field evaluation").
- Searching past records and manuals.
- Conditionals, recurring actions, parallel work, reusable subprocedures,
  multi-day continuation and shift handoff in the guidance itself.
- Offline/degraded mode; headset and device qualification; accessibility
  validation for gloves, PPE and noise.
- Monitoring and export beyond the pilot metrics; production identity
  (OIDC/SSO), rate limits, secret management.
- Whether to write records into an ELN or LIMS (the product replaces
  neither).

## Removed and not planned

- PI or administrator approval, reviewer and administrator approval screens,
  revision approval, rejection and revocation, lab adaptations.
- The old protocols.io, Drive and GitHub connectors, the eLabFTW export and
  the dry-lab metadata lane (a new, narrower Drive link import exists).
- The voice safety report and its hand-off, the approved-manual and
  lab-reference voice searches, the Moss reranker, the xAI web search.

## Non-goals until separately validated

Autonomous protocol approval or modification; autonomous safety decisions or
work-resume authorisation; clinical or medical decision support; unsupervised
equipment control; replacing an ELN or LIMS; any GLP/GMP/GxP, 21 CFR Part 11
or ISO claim from architecture or tests alone.
