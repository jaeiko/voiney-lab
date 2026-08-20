# Product Context: Voice Workflow Agent (Voice Workflow Guide Lab Copilot)

## 1. Product Vision & Mission

**Voice Workflow Agent** is an intelligent, hands-free AI Workflow Agent designed specifically for wet-lab research environments. 
In biological, chemical, and materials science laboratories, researchers must maintain sterile conditions, wear protective gloves (PPE), handle delicate instruments, and execute complex multi-step protocols. Interacting with keyboards, touchscreens, or paper manuals during active experimentation causes contamination risks, protocol deviation, cognitive overload, and distraction.

**Core Mission**:
Empower laboratory researchers to execute approved scientific workflows with zero manual tool-switching, complete traceability, grounded step-by-step guidance, and fail-safe human handoff.

```text
+------------------+       +---------------------+       +-----------------------+
|  GUIDE (Copilot) |  ==>  |  RECORD (Ledger)    |  ==>  |  HANDOFF (Safety Net) |
|  Approved SOPs   |       |  Verbatim Notes     |       |  Structured Incident  |
|  Strict Grounding|       |  Timers & Anomalies |       |  Supervisor Review    |
+------------------+       +---------------------+       +-----------------------+
```

---

## 2. Target Personas & Stakeholders

### Primary Users (Hands-on Researchers)
1. **Undergraduate Student Researchers & Interns**:
   - *Characteristics*: Novice lab experience, high uncertainty, unfamiliar with specific protocol nuances or chemical safety boundaries.
   - *Needs*: Clear, unambiguous step-by-step guidance; immediate clarification on terminology/units; automatic timer tracking; reassurance without guessing.
   - *Pain Points*: Fear of making procedural mistakes; hesitation when encountering unexpected color/texture changes; difficulty recording observation data with gloved hands.
2. **Graduate Researchers (Master's & Ph.D. Candidates)**:
   - *Characteristics*: Proficient in standard laboratory routines, conducting repetitive yet sensitive multi-hour protocols.
   - *Needs*: High-speed hands-free interaction; low voice latency; rapid step confirmation; reliable audit trail for laboratory notebooks; ability to log anomalies on the fly.
   - *Pain Points*: Context switching between benchtop pipette and lab notebook; missed incubation timeframes; incomplete metadata for reproducibility.

### Secondary Users (Laboratory Governance & Safety)
1. **Laboratory Managers**:
   - *Characteristics*: Responsible for lab safety, chemical inventory, equipment calibration, regulatory compliance, and incident resolution.
   - *Needs*: Standardized incident reports with exact timestamps, step numbers, chemical names, and exposure status; zero unapproved protocol deviations.
   - *Pain Points*: Under-reported near misses; vague verbal incident accounts; delayed communication of hazardous spills or malfunctioning equipment.
2. **Principal Investigators (PIs)**:
   - *Characteristics*: Oversee research projects, grant funding, and laboratory integrity.
   - *Needs*: Verifiable protocol adherence; immutable digital experiment records; reproducible experimental results; complete audit summaries.
   - *Pain Points*: Experimental irreproducibility caused by undocumented variations in student execution.

---

## 3. Core Product Principles

### Principle 1: Grounded Operation (Zero Hallucination)
- The agent strictly differentiates between:
  1. **Supported Information**: Facts explicitly present in the active approved SOP, safety catalog, or chemical data sheet.
  2. **Unsupported Information**: Any query or instruction not covered by approved sources. The agent must state clearly: *"This information is not present in the approved protocol/manual. Please consult the lab manager."*
  3. **Uncertain Information**: Ambiguous user speech or partial data must be clarified rather than guessed.
- Numbers, units, chemical formulas, temperatures, and durations must be preserved verbatim.

### Principle 2: Human-Centered Safety (Human-in-the-Loop)
- The AI copilot is an assistant, not an authority.
- The agent **NEVER**:
  - Authorizes resumption of a blocked or halted experiment.
  - Determines that a hazardous chemical spill or contaminated area is "safe".
  - Overrides required PPE or ventilation safety rules.
  - Modifies approved SOP steps dynamically.
- When an abnormal situation, spill, or exposure is detected:
  - Collect: Location, factual summary, urgency, exposure status, equipment/material.
  - Seek explicit human confirmation to submit.
  - Block the active workflow (`blocked_for_handoff`).
  - Queue a structured handoff artifact for the supervisor.

### Principle 3: Workflow-First Architecture
- The foundational entity of the system is the **Workflow State Machine**, not an open-ended conversational session.
- State transitions (start, observation logging, timer start, step completion, block, complete) are strictly server-owned and immutable.
- Conversational inputs trigger deterministic verification gates before state mutations occur.
