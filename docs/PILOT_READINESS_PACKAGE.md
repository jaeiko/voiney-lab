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

- 765 pytest cases + 684 subtests (full local suite), including
  `test_curated_protocol_cascade.py`'s multi-turn scenario tests and
  `test_server_procedure_integration.py`.
- 28 Playwright browser tests (`tests/e2e/`) across desktop and mobile,
  covering the researcher/reviewer/admin workspaces.
- `python scripts/replay_turns.py` and both
  `scripts/evaluate_candidate_a_*.py` evaluators (deterministic,
  provider-free routing checks — see
  `docs/COMMERCIALIZATION_PASS3_REPORT.md` and Phase 16 evidence below).
- A live server smoke test (index, static assets, `/api/workspace/session`,
  `/ws`, `/healthz`, `/readyz`) after every structural change this pass.

No demo shortcut bypasses the production authority model: every completion
still requires the server-computed confirmation gate; no test or demo path
weakens `authorized_completion_step_id`/`authorized_observation_arguments`.

## 2. Field metrics — what's already measurable vs. what would need new code

`runtime_metrics.py` (`RUNTIME_METRICS`) already aggregates, per its explicit
allowlist, without raw audio/transcripts/identities: **route distribution**,
**barge-in cancellation count**, and **turn latency percentiles**.

The rest of the requested KPIs are **already recorded as durable, per-tenant
data** by the existing `ExperimentSession` append-only timeline
(`workspace_store.py`) and experiment reports (`experiment_reports.py`), just
not yet rolled up into the lightweight in-memory metrics view:

| KPI | Where the data already lives |
|---|---|
| Task/workflow completion | `ExperimentSession` lifecycle events (`session_completed`) |
| Pause/resume success | `session_paused`/`session_resumed` timeline events |
| Recovery success | `session_recovered` timeline event; WebSocket reconnect path |
| Step omission / invalid transition rejection | Rejected transitions never produce a `step_completed` event; `procedure_step_events`/experiment timeline only records accepted ones |
| Observation/evidence capture | `observation_recorded`/`evidence_attached` timeline events |
| Documentation completeness | `GET /api/workspace/experiments/{id}/timeline`'s `observation_count`/`evidence_count` |
| Voice admission/rejection | Existing transcript-admission gates (language mismatch, low-confidence) already produce the fixed clarification text; not yet counted |
| Correction/repeat rate | "다시 말해줘" (repeat) turns are routed deterministically but not yet counted separately from other read-only turns |
| Session abandonment | Absence of a `session_completed`/`session_stopped` event after a period — needs a query, not new capture |

**Not implemented this pass**: a rollup job/endpoint that aggregates these
existing durable events into the same kind of privacy-safe summary
`runtime_metrics.py` provides for routes/latency. This was deliberately
deferred rather than rushed — adding new aggregation touchpoints across
`server.py`'s turn-handling paths late in a long session carries real risk
in a safety-critical codebase, and every one of the KPIs above is already
recoverable from existing durable records via a read-only query. Treat this
as a scoped, low-risk follow-up (a query script or admin-analytics endpoint
addition), not a blocker to running a pilot.

## 3. Pilot package materials

### Pre-study checklist (operator)
- [ ] `python -m pytest -q` and `npx playwright test` both green on the exact commit being piloted.
- [ ] `GET /readyz` returns `200` with the expected capability flags for this deployment.
- [ ] Protocol to be used is an **Approved Protocol Revision** (not a development/draft activation) unless the pilot explicitly intends to demonstrate the review flow.
- [ ] Backup taken per `docs/DEPLOYMENT_RUNBOOK.md` before the session.
- [ ] Participant briefed per the privacy/data-handling explanation below.

### Researcher quick-start
1. Select the approved protocol from the researcher workspace.
2. Press "세션 시작" (Start session) and wait for `LISTENING`.
3. Speak naturally — "프로토콜 시작해줘" to begin, "현재 단계 알려줘" to hear the current step again, "다음 단계로 넘어가줘" only after you have actually completed the step.
4. Use the observation/evidence controls on the bench workspace for anything you want on the record — voice or manual, either is fine.
5. "일시정지" (Pause) at any point; resume later by reselecting the same experiment session.

### Reviewer quick-start
1. Open the reviewer workspace; new/changed sources appear in the inbox.
2. Read the diff before deciding — accepting OCR text is not the same as approving a protocol.
3. Approve, reject, or revoke; every decision is append-only and visible in the audit trail.

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
Use the mapping in Section 2. A pilot report should state each KPI's value
and, where it comes from durable-but-not-yet-aggregated data, the exact
query used to derive it (for reproducibility).

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
Raw audio and full transcripts are not retained in analytics. Observations
and evidence you explicitly record are stored as `observation_only` /
`not_interpreted` — they become part of your experiment record but never
silently become new instructions. Model reasoning and provider secrets are
never logged. See `README.md`'s "Security and privacy boundaries" section
for the complete, code-enforced list.

### Known limitations (state these to every pilot participant)
- Controlled-pilot system: not a validated GLP/GMP/clinical system, not a
  full ELN/LIMS, not an autonomous scientist, not a safety authority.
- No external integration (Drive, GitHub, protocols.io, OIDC against a real
  IdP, eLabFTW, OCR) has been live-tested in this environment — see
  `docs/LAB_WORKFLOW_OS_IMPLEMENTATION_REPORT.md`'s classification table.
  xAI STT/TTS and the LLM structured-analysis endpoint **were** live-tested
  this pass (Section 4 below).
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

## 4. Voice acceptance — provider-backed evidence from this pass

Beyond the deterministic, provider-free evaluators (Phase 12), this pass
performed genuine live validation (Phase 16) of the two capabilities a real
pilot session actually depends on moment-to-moment:

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
