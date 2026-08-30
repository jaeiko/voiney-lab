# Wet-Lab Pilot Readiness Package

This documents what the product can support for a real, controlled wet-lab
pilot, and what still requires a human field study. It does not claim any
human field validation happened — none did in this environment.

## 1. Pilot flow — validated without bypassing workflow controls

The full flow (Protocol Source → analysis → human review boundary → Approved
Protocol Revision → ExperimentSession → deterministic START → voice guidance
→ protocol questions → timer → Observation → Evidence → Pause → Resume →
disconnect/recovery → Timeline → completion → report → optional ELN
boundary) is exercised end to end by:

- The complete offline pytest gate, including production WebSocket, persistence,
  source, identity, connector, recovery, and privacy boundaries.
- The Playwright suite across desktop and mobile, including a real
  reviewer-approval → researcher checkpoint journey and a microphone-denied
manual execution journey.
- `python scripts/replay_turns.py` and both
  `scripts/evaluate_candidate_a_*.py` evaluators (deterministic,
  provider-free routing checks — see
  `docs/COMMERCIALIZATION_PASS3_REPORT.md` and Phase 16 evidence below).
- A live server smoke test (index, static assets, `/api/workspace/session`,
  `/ws`, `/healthz`, `/readyz`) after every structural change this pass.

No demo shortcut bypasses the production authority model: every completion
still requires the server-computed confirmation gate; no test or demo path
weakens `authorized_completion_step_id`/`authorized_observation_arguments`.

## 2. Field metrics — pilot-ready read-only rollup

`runtime_metrics.py` (`RUNTIME_METRICS`) already aggregates, per its explicit
allowlist, without raw audio/transcripts/identities: **route distribution**,
**barge-in cancellation count**, and **turn latency percentiles**.

The admin workspace and `GET /api/workspace/admin/pilot-metrics` now provide a
tenant-scoped, read-only rollup. The endpoint combines durable
`ExperimentSession` event metadata with retention-bounded analytics; it never
returns raw audio, transcripts, user identifiers, free text, secrets, or model
reasoning.

| KPI | Definition and source |
|---|---|
| Voice turns / successful turns | Accepted Cascade endpoints and non-greeting `turn.done` events; the derived rate is reported only when turns exist |
| Clarification / repeat use | Deterministic clarification plans, explicit current-step repeat requests, and consecutive repeated utterances; no utterance text or hash is persisted |
| STT failures | Provider failures plus empty/non-speech admission failures |
| Ambiguous/blocked mutations | Ambiguous completion commands and state-changing requests refused by language, speaker, checkpoint, or deterministic policy gates |
| Interruption quality | Ignored acoustic candidates, confirmed barge-ins, and playback-only interruption events are separate counters |
| Speaker ambiguity | Unknown-speaker mutation rejections and overlapping-speaker mutation ambiguity events; no biometric voiceprint exists |
| Completed workflows | Experiment sessions whose durable state is `completed` |
| Completed steps | Durable `step_completed` events, including idempotent replay protection |
| Failed commands | Empty/non-speech admissions and bounded turn-processing failures recorded as privacy-safe analytics |
| Recovery events | Durable `session_recovered` timeline events |
| Persistence failures | Explicit persistence attempts whose workflow state was rejected or rolled back; this is not labeled “unintended mutation” |
| User actions | Retained server-accepted workflow-turn samples |
| Completion rate | Completed experiment sessions divided by all tenant experiment sessions |
| Session duration | Wall-clock time from durable session start to completion for completed sessions; average/max are descriptive, not efficiency targets |
| Observation/evidence capture | Durable `observation_recorded` and `evidence_attached` event totals plus per-session timeline counts |
| Pause/resume activity | Durable `session_paused` and `session_resumed` event totals |
| Manual fallback actions | Durable manual protocol starts/completions, bench pause/resume/stop, manual observations, and evidence attachments; generic clicks are not counted |

The response labels its measurement window: session/event counters are lifetime
for retained durable records, while voice and failure counters follow analytics
retention. There is no claim that a blocked attempt was an unintended mutation;
false mutation remains a separately validated safety outcome. Step omission,
abandonment, and arbitrary screen interactions have no dedicated counter and
must not be inferred from missing events.

## 3. Pilot package materials

### Pre-study checklist (operator)
- [ ] `python -m pytest -q` and `npx playwright test` both green on the exact commit being piloted.
- [ ] `GET /readyz` returns `200` with the expected capability flags for this deployment.
- [ ] Protocol to be used is an **Approved Protocol Revision** (not a development/draft activation) unless the pilot explicitly intends to demonstrate the review flow.
- [ ] Backup created **and verified** with `scripts/pilot_state_backup.py` per
  `docs/DEPLOYMENT_RUNBOOK.md` before the session.
- [ ] Participant briefed per the privacy/data-handling explanation below.

### Researcher quick-start
1. Select the approved protocol from the researcher workspace.
2. Press "세션 시작" (Start session) and wait for `LISTENING`.
3. Speak naturally — "프로토콜 시작해줘" to begin, "현재 단계 알려줘" to hear the current step again, "다음 단계로 넘어가줘" only after you have actually completed the step.
4. Use the observation/evidence controls on the bench workspace for anything you want on the record — voice or manual, either is fine.
5. "일시정지" (Pause) at any point; resume later by reselecting the same experiment session.

If the microphone cannot be used after the server accepts the session, the
state changes to **수동 실행** instead of ending the experiment. Confirm the
approved step/version on screen, use **프로토콜 시작** if the fresh workflow is
still ready, then use current-step completion, pause, stop, observation, and
evidence controls. Repeated STT failures, severe noise, or unavailable TTS are
also reasons to press **음성 없이 계속** and use this screen path. If the
approved step or source is not visible, stop using the agent and return to the
approved source protocol.

### Reviewer quick-start
1. Open the reviewer workspace; new/changed sources appear in the inbox.
2. Read the diff before deciding — accepting OCR text is not the same as approving a protocol.
3. Approve, request a revision, or stop future use; every decision is append-only
   and visible in the audit trail.

The reviewer checks source ambiguity, represented structure, changed steps,
material/concentration/time/equipment changes, warnings, source-defined human
checkpoints, and whether the exact represented revision is fit for execution.
The reviewer is not made an experiment safety guarantor, regulatory certifier,
or autonomous scientific authority. OCR/source-text acceptance only accepts an
extraction for later analysis; execution approval authorizes one exact revision;
stopping future use prevents new sessions while preserving existing history.

### Admin setup checklist
- [ ] Confirm the OIDC configuration (not development identity) is active if this is anything beyond a fully controlled internal pilot.
- [ ] Confirm connector configuration state is what's expected — the admin workspace explicitly labels each as configured-but-not-live-tested where applicable.
- [ ] Set the analytics retention policy deliberately (1–3650 days).

### Test session protocol
Run the pilot flow (Section 1) once, end to end, with a real researcher, on
a real (but low-stakes/fictional-acceptable) protocol, before any multi-user
or multi-session pilot. Record every deviation from expected behavior using
the incident template below, not just "it worked" / "it didn't."

### Measurable KPI definitions
Capture the admin pilot-metrics response at the start and end of the agreed
pilot window. Report both snapshots, their difference where appropriate, the
configured analytics-retention period, and the explicit limitations above.

### Incident/error recording template
```
Date/time:
Session ID (from the UI, not the internal principal ID):
Step/screen where it occurred:
Expected behavior:
Actual behavior:
Was any workflow state incorrectly mutated? (must be "no" — flag immediately if not)
Severity: [see categories below]
Reproducible? (steps if yes)
```

### Post-session interview questions
1. Did the system ever advance, complete, or approve something without your explicit confirmation?
2. Was the current step always clear? If not, when did it become unclear?
3. Did Pause/Resume/recovery behave the way you expected?
4. Was voice recognition accurate for your speech patterns? Any repeated corrections?
5. Would you trust this system's timeline as your experiment record? Why or why not?
6. What would you change before using this unsupervised?

### Privacy / data-handling explanation (for participants)

Stored under the lab/workspace boundary: exact source/revision identity,
session lifecycle and step events, explicit observations, explicitly attached
evidence bytes and metadata, report/export events, approval/audit events,
connector configuration status without credential values, and allowlisted
operational aggregates. Access uses fixed lab roles and tenant checks.

Not retained by the pilot analytics path: raw microphone audio, transcript
content, model prompts/reasoning, provider tokens/API keys, free-form protocol
text, user identifiers, or biometric voiceprints. Audio/transcript data is sent
ephemerally to the configured STT/TTS/model providers when those features are
used; the deployment owner must approve the provider/data agreement before a
real pilot. Diagnostic recordings remain disabled by default and require
separate consent and bounded retention.

Analytics retention is configurable per lab from 1–3650 days and enforcement
purges expired analytics. Durable protocol/session/report/audit records are
append-only and have no general per-record deletion UI; the organization must
define whole-deployment/tenant deletion, legal hold, storage encryption, and
backup retention before participants begin. Reports and evidence can be
exported through their authorized product paths. Evidence is stored as
`not_interpreted`; observations are observation-only; neither silently becomes
instruction authority. This is a product boundary, not a legal-compliance claim.

### Known limitations (state these to every pilot participant)
- Controlled-pilot system: not a validated GLP/GMP/clinical system, not a
  full ELN/LIMS, not an autonomous scientist, not a safety authority.
- No external integration (Drive, GitHub, protocols.io, OIDC against a real
  IdP, interactive Google login, eLabFTW, OCR) was live-tested in this pass.
  Historical bounded xAI STT/TTS and analysis-connectivity evidence is retained
  in Section 4 and the capability matrix; it is not current field evidence.
- The legacy `procedures.py` tutorial lane exists but is off by default and
  should not be enabled for a pilot unless specifically intended.

### Abort / stop criteria
Stop the pilot session immediately if:
- Any workflow step is marked complete, approved, or written to an ELN
  without the participant's explicit confirmation having occurred.
- An observation or evidence entry is treated as an instruction that changes
  subsequent guidance.
- The system produces safety-relevant guidance that contradicts the
  approved protocol's own text.

### Incident severity categories
- **Critical**: any of the abort criteria above occurred.
- **High**: workflow state became inconsistent (wrong step, lost recovery)
  but no incorrect mutation reached the durable record.
- **Medium**: voice recognition/UX friction that required a workaround but
  did not affect correctness.
- **Low**: cosmetic or minor wording issues.

## 4. Voice acceptance — retained provider-backed evidence

Beyond the deterministic, provider-free evaluators, Commercialization Pass 4
performed genuine live validation of the two capabilities a real pilot session
depends on moment-to-moment. This evidence was not re-created during the current
productization phases:

- **xAI TTS**: real `POST /v1/tts` call via `voice_workflow_agent.server.synthesize`, 56,016 bytes of PCM returned for a short Korean phrase.
- **xAI STT**: the TTS output was fed back through `voice_workflow_agent.server.transcribe` (real `POST /v1/stt`), returning `response_status=200`, `detected_language="ko"` (correct), transcript length matching the source text, in ~415ms.
- **LLM structured protocol analysis**: a live `POST /v1/chat/completions` call returned `200 OK` from the real model; the response was rejected by strict downstream evidence-retention validation because the test document was a deliberately minimal one-line synthetic PDF, not a realistic multi-page protocol — a test-input artifact, not an integration defect. The full parse/verify pipeline is otherwise covered by 100+ existing tests against a fake model.

This upgrades xAI STT and TTS from "implemented, not live-tested" to
**live-tested** in the integration classification. See
`docs/COMMERCIALIZATION_PASS4_REPORT.md` for the updated table.

## What this package does not and cannot claim

No human wet-lab study occurred in this environment. Every KPI above is a
capability/instrumentation statement, not a result. The first real pilot
session is the actual field validation step.
