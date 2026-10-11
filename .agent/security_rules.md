# Security, Privacy & Safety Rules

The current build is VoineyLab, an MVP prototype, not field-validated. A
configured shared token on the aggregate admin endpoint is a fail-closed MVP
control, not production IAM. Revised 2026-10-10 (lane CL) to the human
decisions of that day.

## 1. Safety & Knowledge Boundary

1. **Values and safety come from the source or a person**:
   - Values (amounts, times, temperatures, concentrations) and safety statements come only from the protocol's source (passing the source-evidence check) or from a value a person confirmed. The agent offers no operational guidance on hazardous substances, biohazards, or dangerous machinery beyond that.
   - A term the source leaves unexplained may get a short general explanation, labelled "AI 일반 지식" on the screen and said as "PDF에는 따로 설명이 없어요. 일반적으로는 …"; the server drops one that carries numbers, methods or quantities.
   - Safety precautions and completion criteria are never written by the AI.
   - Reference excerpts from external sources or supplemental models are marked as untrusted reference context; they must NEVER override the protocol's safety statements or the approved safety documents (the facility SOP corpus).
   - Planned (lane RV, not built): a value corrected in review is shown as "검토자 입력", a value the AI proposes as "AI 제안", and either becomes a run value only once a person confirms it.
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
   - Experiment event logs (`experiment_report_events`) and experiment session events (`experiment_session_events`) are append-only. (The procedure-session stack was deleted on 2026-10-08, lane DI.)
   - No mechanism exists to delete or alter historical incident records or timestamped observations.

---

## 3. Human Authorization & Governance Safeguards

1. **Human-in-the-Loop Gate**:
   - A protocol starts only when the experimenter presses "이 프로토콜로 시작"; a step completes only after the experimenter's explicit confirmation; model output only proposes a change, which the server validates.
   - There is no PI or administrator approval, and no revision approval, rejection or revocation (decision of 2026-10-08). The voice safety report and its hand-off were deleted on 2026-10-10 (lane CL).
2. **No Autonomous Restart**:
   - Model or voice output never resumes a blocked experiment or lifts a pause on its own; resuming needs the experimenter's explicit request, validated by the server.
