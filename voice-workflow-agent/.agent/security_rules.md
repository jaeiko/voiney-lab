# Security, Privacy & Safety Rules

The current build is a controlled-pilot prototype. A configured shared token on
the aggregate admin endpoint is a fail-closed MVP control, not production IAM.

## 1. Safety & Approved Knowledge Boundary

1. **Approved Knowledge Exclusivity**:
   - The agent is strictly prohibited from offering operational guidance on hazardous substances, biohazards, or dangerous machinery without an approved, verified document source in `approved_catalog.sqlite` or active protocol definitions.
   - Reference excerpts retrieved from external sources or supplemental models are strictly marked as untrusted reference context; they must NEVER override active SOP safety constraints.
2. **Emergency Protocol Precedence**:
   - Immediate safety hazards (chemical burns, toxic fumes, fire, explosions, medical emergencies) immediately trigger deterministic emergency stop instructions:
     1. Stop work immediately.
     2. Evacuate/step away from the hazard zone.
     3. Contact facility emergency channels / lab manager immediately.

---

## 2. Research Data Privacy & Confidentiality

1. **No Sensitive Data Leaks**:
   - Do NOT transmit proprietary research compound formulas, unpublished patent data, or personal researcher identifiers to third-party endpoints unless explicitly authorized and bounded by enterprise data privacy agreements.
2. **API Key Hygiene**:
   - API keys (`XAI_API_KEY`, etc.) must NEVER be logged in server logs, rendered in the browser UI, or committed to version control.
   - All external model and STT/TTS calls are executed purely server-side.
   - Do not use credential values in metric labels, exception details, URLs, or
     browser storage. Compare the admin token with constant-time digests and clear
     the UI input after each request.
3. **Audio and Transcript Minimization**:
   - Raw audio is not retained by default. Diagnostic retention is opt-in, bounded,
     ignored by Git, and prohibited for private lab work without an approved policy.
   - Operational aggregates must exclude transcripts, free-form wording, protocol
     titles, report/session IDs, prompts, and model reasoning.
4. **External Image Boundary**:
   - Never hotlink provider image URLs. Display requires a rights label, HTTPS and
     SSRF validation, size/MIME/dimension checks, and same-origin proxying.
   - When rights or bytes cannot be validated, emit only a cited source link.
5. **Audit Trail Immutability**:
   - Experiment event logs (`experiment_report_events`) and procedure session records (`procedure_sessions`) are append-only.
   - No mechanism exists to delete or alter historical incident records or timestamped observations.

---

## 3. Human Authorization & Governance Safeguards

1. **Human-in-the-Loop Gate**:
   - Submitting an incident report requires explicit verbal confirmation (`네, 제출해 주세요`, `Yes, submit the report`).
   - The agent cannot self-submit or automatically escalate reports without human approval.
2. **No Autonomous Restart**:
   - Once a workflow is placed into `blocked_for_handoff`, the agent software cannot unblock or resume the workflow.
   - Only a designated human laboratory manager or PI can authorize an experiment restart after reviewing the handoff artifact.
