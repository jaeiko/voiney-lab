# Current Capability and Backlog Matrix

Date: 2026-08-30

Product: Voice Workflow Agent — hands-free Bench Execution Layer

Maturity: **Controlled Pilot Ready — Engineering / Field-Unvalidated**

This is the single current capability ledger. Code and current tests outrank
historical reports. “Live provider validated” means a real external call was
recorded; “field validated” means evidence from actual target users in a lab.
Contract tests and simulations never satisfy either column.

Status vocabulary: **IMPLEMENTED**, **PARTIALLY IMPLEMENTED**, **MISSING**,
**EXTERNAL/LIVE VALIDATION REQUIRED**, and **INTENTIONALLY DEFERRED / NON-GOAL**.
“Historical” live evidence was not repeated in this pass.

## Bench execution and voice

| Capability | Current status | Implemented | Automated / contract tested | Live provider validated | Field validated | Deferred | Notes |
|---|---|---:|---:|---:|---:|---:|---|
| Browser audio capture | IMPLEMENTED | Yes | Yes | N/A | No | No | AudioWorklet sends 16 kHz mono PCM; raw audio is not retained by default. |
| WebRTC VAD and endpointing | IMPLEMENTED | Yes | Yes | N/A | No | No | Server-owned frame admission, endpoint, cooldown, and stale-generation fences. |
| Noisy-lab interruption gate | IMPLEMENTED | Yes | Yes | No | No | No | Adaptive ambient floor and playback-onset guard are synthetic-tested; a consented noisy-lab study remains required. |
| xAI batch STT | EXTERNAL/LIVE VALIDATION REQUIRED | Yes | Yes | Historical bounded call | No | No | Provider path has historical Korean round-trip evidence; current credentials, accents, noise, and facility performance remain unvalidated. |
| xAI segmented TTS | EXTERNAL/LIVE VALIDATION REQUIRED | Yes | Yes | Historical bounded call | No | No | Provider path has historical PCM evidence; current availability and bench intelligibility remain unvalidated. |
| Intentional barge-in | IMPLEMENTED | Yes | Yes | No | No | No | Candidate and confirmed interruption are distinct; priority stop/pause is fail-safe. |
| Playback/workflow/persistence outcome separation | IMPLEMENTED | Yes | Yes | N/A | No | No | Interrupting playback cannot undo an already committed checkpoint. |
| Diarization normalization | PARTIALLY IMPLEMENTED | Yes | Yes | No | No | Yes | Provider-neutral segment model exists; current xAI diarization behavior needs live validation. It is never authentication. |
| Session participant mapping | PARTIALLY IMPLEMENTED | Backend seam | Yes | N/A | No | Yes | Only explicit human confirmation associates a session-scoped acoustic label with a rostered member; no full participant setup UI. |
| Unknown-speaker mutation policy | IMPLEMENTED | Yes | Yes | N/A | No | No | Unknown speakers may ask read-only questions but cannot silently mutate once labels are confirmed; stop remains fail-safe. |
| Overlapping speech handling | IMPLEMENTED | Yes | Yes | No | No | No | Overlapping mutating speech is blocked and asks speakers to retry one at a time. |
| Korean reviewer-approved sidecar | IMPLEMENTED | Yes | Yes | N/A | No | No | Exact approved sidecar has higher presentation authority than automatic translation. |
| Automatic Korean presentation | IMPLEMENTED | Yes | Yes | No | No | No | Enabled by default for Korean, mechanically checks scientific tokens, labels output `자동 번역`, and fails closed to exact source. |
| Translation cache | IMPLEMENTED | Yes | Yes | N/A | No | No | Bounded process-local LRU keyed by immutable source/revision, step, language, and policy identity; authority never changes. |
| Current/next/repeat/detail Korean projection | IMPLEMENTED | Yes | Yes | N/A | No | No | Korean primary is shared across start, current, preview, repeat, detail, and post-completion guidance; exact English remains secondary. |
| Ordinary “not done” vs human checkpoint | IMPLEMENTED | Yes | Yes | N/A | No | No | Ordinary steps stay put naturally; source-defined checkpoints retain exact repeat behavior with no invented maximum. |
| Manual voice-degraded execution | IMPLEMENTED | Yes | Yes, including real browser | N/A | No | No | Accepted session and approved step remain visible; manual start, completion, pause, stop, observation, and evidence remain available. |
| Researcher primary information architecture | IMPLEMENTED | Yes | Yes, desktop/mobile | N/A | No | No | Shows experiment/version, current action, allowed commands, canonical save outcome, and stop/recovery path. |
| Keyboard, screen-reader, touch, reduced-motion P0 | IMPLEMENTED | Yes | Yes | N/A | No | No | Focus, dialog trap/return, text status, announcements, reduced motion, and core touch targets are covered; no WCAG certification claim. |
| Noisy-lab/accent/accessibility field study | EXTERNAL/LIVE VALIDATION REQUIRED | Study plan only | Synthetic evaluation only | No | No | Yes | Must be run with consented target users, devices, PPE, and facility acoustics. |

## Protocol, records, review, and pilot operations

| Capability | Current status | Implemented | Automated / contract tested | Live provider validated | Field validated | Deferred | Notes |
|---|---|---:|---:|---:|---:|---:|---|
| Local PDF onboarding | IMPLEMENTED | Yes | Yes | N/A | No | No | Immutable bytes/hash/pages, bounded parsing, long-running analysis status, and corrupt/encrypted/unsupported failure paths. |
| Structured protocol analysis | EXTERNAL/LIVE VALIDATION REQUIRED | Yes | Fake-backed | Historical connectivity only | No | No | Strict typed/evidence validation exists; no current realistic live full-document validation. |
| OCR lifecycle | EXTERNAL/LIVE VALIDATION REQUIRED | Yes | Contract-tested | No | No | No | Trusted injected provider, page-complete evidence, human accept/reject, and separate execution approval; no bundled/live OCR provider. |
| Reviewer resolution, diff, history, and approval | IMPLEMENTED | Yes | Yes, including browser | N/A | No | No | Changed structure/material/time/equipment/warnings and consequences are reviewed; approval is exact-revision and append-only. |
| Reviewer assignment/custom policy | MISSING | No | No | N/A | No | Yes | Fixed reviewer role and decision history exist; assignment queues, signatures, and custom approval policies are post-pilot work. |
| Lab adaptation domain model/API | IMPLEMENTED | Yes | Yes | N/A | No | No | Immutable child revision, typed changes, provenance, diff projection, and mandatory review are enforced. |
| Lab adaptation authoring UI | PARTIALLY IMPLEMENTED | Review projection only | API/browser projection tests | N/A | No | Yes | No safe guided authoring UI yet; free-form editing was intentionally not added. |
| Observation capture | IMPLEMENTED | Yes | Yes | N/A | No | No | Voice/manual append-only observations never become instruction authority automatically. |
| Observation-to-knowledge promotion | IMPLEMENTED | Yes | Yes | N/A | No | No | Separate reviewer promotion with provenance and non-authoritative draft state. |
| Evidence capture/download | IMPLEMENTED | Yes | Yes | N/A | No | No | Allowlisted bounded files, tenant gate, SHA-256/size verification, and `not_interpreted` status. |
| Append-only reporting and export | IMPLEMENTED | Yes | Yes | N/A | No | No | Timeline plus JSON/Markdown/CSV/DOCX; not an electronic-signature or regulated records claim. |
| Recovery/resume | IMPLEMENTED | Yes | Yes, including browser | N/A | No | No | Exact revision/current step/completed prefix restore; pending confirmations, conversation, and active timers are intentionally not restored. |
| Analytics retention control | IMPLEMENTED | Yes | Yes | N/A | No | No | Tenant admin sets 1–3650 days for content-free analytics; durable append-only records require organization policy. |
| Backup/verify/restore | IMPLEMENTED | Yes | Yes | N/A | No | No | Allowlisted SQLite/object/evidence archive with checksums and safe extraction; deployment restore drill still required. |
| Pilot KPI rollup | IMPLEMENTED | Yes | Yes | N/A | No | No | Tenant-scoped voice, clarification, repeat, STT, mutation, interruption, speaker, workflow, duration, recovery, capture, and manual-action counts. |
| Pilot runbook and recovery matrix | IMPLEMENTED | Yes | Documentation reviewed | N/A | No | No | Covers degraded voice, network, stale state, persistence, restore boundaries, and privacy-safe incident evidence. |
| Controlled wet-lab pilot | EXTERNAL/LIVE VALIDATION REQUIRED | Software package ready | Automated simulation | No | No | Yes | One supervised low-risk protocol is the next evidence step; efficiency targets await baseline measurement. |

## Identity, membership, and administration

| Capability | Current status | Implemented | Automated / contract tested | Live provider validated | Field validated | Deferred | Notes |
|---|---|---:|---:|---:|---:|---:|---|
| Tenant-scoped principal/RBAC | IMPLEMENTED | Yes | Yes, including cross-tenant denial | N/A | No | No | Fixed researcher/reviewer/lab-admin/org-admin roles; server derives tenant from authenticated principal. |
| Lab membership administration | PARTIALLY IMPLEMENTED | Single-workspace membership | Yes | N/A | No | Yes | Admin can manage active roles; one principal currently resolves to one organization per session, not a multi-lab chooser. |
| Development login through principal seam | IMPLEMENTED | Yes | Yes | N/A | No | No | Development profiles use the same principal/membership checks and are prohibited in operational scope. |
| Generic operational OIDC bearer auth | EXTERNAL/LIVE VALIDATION REQUIRED | Yes | Contract-tested with generated keys | No | No | No | Signature/issuer/audience/time/claims and membership are fail-closed; a real IdP deployment is not validated. |
| Google OIDC security foundation | PARTIALLY IMPLEMENTED | Library/scaffold | Yes | No | No | Yes | State, nonce, PKCE, server exchange seam, invitation resolution, secure-cookie contract, and single-use challenge store exist. |
| Interactive real Google login | MISSING | No HTTP login/callback/session route | No production-boundary test | No | No | Yes | Requires durable shared challenge/session storage, real token verifier/transport, deployment credentials, and live validation. |
| Multi-researcher experiment participation UX | PARTIALLY IMPLEMENTED | Roster/policy seam only | Yes | No | No | Yes | No attendance/participant-selection UI or multi-user concurrency workflow; no biometric voiceprints are created. |
| Admin members/login/connections/retention/health UI | IMPLEMENTED | Yes | Yes, including browser | N/A | No | No | Human-facing primary copy; technical identifiers stay out of the primary operational presentation. |
| Failed connection/export visibility | PARTIALLY IMPLEMENTED | Connector/write-back status and audit | Yes | N/A | No | Yes | Recent connector failures are visible; no centralized multi-process incident console. |

## Connectors, ELN, and computational metadata

| Capability | Current status | Implemented | Automated / contract tested | Live provider validated | Field validated | Deferred | Notes |
|---|---|---:|---:|---:|---:|---:|---|
| protocols.io read-only import | EXTERNAL/LIVE VALIDATION REQUIRED | Yes | Contract-tested | No | No | No | Allowlisted identity/origin; import creates a review-required revision, never an executable shortcut. |
| Google Drive/Shared Drive import | EXTERNAL/LIVE VALIDATION REQUIRED | Yes | Contract-tested | No | No | No | Read-only roots, PDF/Docs export, cursor/version fences; no live OAuth or file read. |
| GitHub source import/webhook | EXTERNAL/LIVE VALIDATION REQUIRED | Yes | Contract-tested | No | No | No | Exact repository/ref/path/commit, signed replay-protected webhook; no live GitHub App installation. |
| eLabFTW write-back | EXTERNAL/LIVE VALIDATION REQUIRED | Yes | Contract-tested | No | No | No | Completed matching session/report/revision, explicit confirmation, idempotency, HTTPS/SSRF checks; CSV/manual handoff remains fallback. |
| Generic ELN/LIMS SDK/maps | MISSING | No | No | No | No | Yes | Current product has one eLabFTW adapter and exports, not a generic synchronization platform. |
| Per-lab terminology pack | PARTIALLY IMPLEMENTED | Protocol-derived STT keyterms | Yes | No | No | Yes | No durable lab-owned terminology editor/source audit yet; arbitrary model memory is not authority. |
| Multilingual facility validation | EXTERNAL/LIVE VALIDATION REQUIRED | Korean/English paths; limited Vietnamese labels | Automated language tests | No | No | Yes | Facility-specific terminology and user validation are required before expansion. |
| Snakemake/Nextflow metadata import | IMPLEMENTED | Metadata-only | Yes | N/A | No | No | Pinned source inspection and review; imported code is never executed. |
| Dry-lab wet/dry provenance link | IMPLEMENTED | Yes | Yes | N/A | No | No | Requires visible durable session and approved metadata revision; execution fields remain false. |
| Seqera launch / arbitrary code execution | INTENTIONALLY DEFERRED / NON-GOAL | No | Interface non-execution tested | No | No | Yes | No supported launch path in the voice server. |

## Operations and long-term backlog

| Capability | Current status | Implemented | Automated / contract tested | Live provider validated | Field validated | Deferred | Notes |
|---|---|---:|---:|---:|---:|---:|---|
| `/healthz` and `/readyz` | IMPLEMENTED | Yes | Yes | N/A | No | No | Liveness and local configuration readiness; probes intentionally make no billable provider call. |
| Runtime route/tool/latency metrics | IMPLEMENTED | Process-local bounded registry | Yes | N/A | No | No | Content-free p50/p95 snapshots; process restart loses the in-memory timing window. |
| Durable pilot operational metrics | PARTIALLY IMPLEMENTED | Tenant SQLite analytics/events | Yes | N/A | No | Yes | Useful for a controlled single-process pilot; no warehouse, HA, or distributed aggregation. |
| Degraded network behavior | PARTIALLY IMPLEMENTED | Visible failure, reconnect, stale fences | Yes | N/A | No | Yes | No offline queue or disconnected mutation mode; approved source/manual procedure remains the fallback. |
| Privacy-safe support/debug bundle | MISSING | No standalone bundle | No | N/A | No | Yes | Admin metrics/security and incident template exist; a portable bundle needs deployment policy and additional allowlisting. |
| Conditional protocols | INTENTIONALLY DEFERRED / NON-GOAL | Fail-closed detection only | Yes | N/A | No | Yes | Execution semantics require separate source/reviewer/safety validation. |
| General repeat structures | PARTIALLY IMPLEMENTED | Source-defined human checkpoint loop | Yes | N/A | No | Yes | Exact reviewed checkpoint repeats work; arbitrary nested/bounded loop semantics remain deferred. |
| Parallel branches | INTENTIONALLY DEFERRED / NON-GOAL | Fail-closed detection only | Yes | N/A | No | Yes | Not executable. |
| Multi-day protocols | INTENTIONALLY DEFERRED / NON-GOAL | Basic durable resume only | Recovery tested | N/A | No | Yes | No time-window, storage-condition, or day-boundary authority model. |
| Cross-shift handoff | INTENTIONALLY DEFERRED / NON-GOAL | Pause/report primitives only | Primitive tests | N/A | No | Yes | No formal custody/acceptance workflow. |
| Inventory context | INTENTIONALLY DEFERRED / NON-GOAL | Asset metadata only | Yes | N/A | No | Yes | Not inventory management and never infers availability. |
| Equipment context | PARTIALLY IMPLEMENTED | Approved source/safety/asset metadata | Yes | N/A | No | Yes | No live instrument state or control. |
| Barcode cards | INTENTIONALLY DEFERRED / NON-GOAL | No scanner workflow | No | N/A | No | Yes | Requires identity, labeling, and facility validation. |
| Automation/instrument/robot control plane | INTENTIONALLY DEFERRED / NON-GOAL | No | Non-execution boundaries tested | No | No | Yes | Requires a separate validated safety architecture. |
| Domain packages | INTENTIONALLY DEFERRED / NON-GOAL | Wet-lab wedge only | N/A | N/A | No | Yes | Multi-industry expansion is outside the controlled-pilot scope. |
| Native/Realtime speech-to-speech migration | INTENTIONALLY DEFERRED / NON-GOAL | No | Cascade-only contract tested | No | No | Yes | Cascade is the sole authoritative voice path. |
| Broad general-purpose web search | INTENTIONALLY DEFERRED / NON-GOAL | Bounded supplementary research only | Yes | Limited optional adapters | No | Yes | External context never outranks protocol/safety evidence or mutates state. |
| Autonomous approval/science/safety decisions | INTENTIONALLY DEFERRED / NON-GOAL | No | Negative gates tested | N/A | No | Yes | Human protocol approval and scientific checkpoint judgment remain authoritative. |
| Regulatory compliance marketing | INTENTIONALLY DEFERRED / NON-GOAL | No | N/A | N/A | No | Yes | No GLP/GMP/GxP/21 CFR Part 11 or clinical validation claim. |
| ELN/LIMS replacement | INTENTIONALLY DEFERRED / NON-GOAL | No | N/A | N/A | No | Yes | Existing systems remain downstream systems of record. |
