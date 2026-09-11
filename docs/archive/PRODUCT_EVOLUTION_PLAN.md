# Product Evolution Plan: Voice Workflow Agent Commercial AI Product

**Document**: `docs/PRODUCT_EVOLUTION_PLAN.md`  
**Author**: Principal AI Engineer, AI Agent Architect & Startup CTO  
**Target Milestone**: Commercial Wet-Lab AI Agent Product Platform  
**Date**: August 20, 2026  

---

## 1. Executive Product Vision & Evolution Strategy

The **Voice Workflow Agent** has established a rock-solid, production-grade foundation: deterministic server-owned workflow gates, WebRTC VAD voice activity windowing, real-time citation grounding, fail-safe human handoff worker queues, and 100% automated test coverage.

To transition from an engineering-hardened platform into a **commercially differentiated, daily-used AI Workflow Agent**, the system must evolve along the real-world operational axis of scientific wet-lab research.

### Core Product Objectives:
1. **Maximize Researcher Usefulness**: Provide contextual explanations, proactive mistake prevention, and scientific rationale during active bench work.
2. **Maximize Daily Usability**: Support multi-day experiment lifecycles, session resumption ("Continue yesterday's experiment"), and seamless cross-shift handovers.
3. **Maximize Commercial Potential**: Provide cryptographic protocol version tracking and audit linkage required for GLP/GMP pharmaceutical compliance.
4. **Maximize Technical Differentiation**: Introduce deterministic server-side Workflow Graph condition evaluation (DAG) while upholding zero-hallucination safety principles.

---

## 2. Comprehensive Evaluation of Priority Evolution Areas

```
+-------------------------------------------------------------------------------------------------------+
|                                    COMMERCIAL AI WORKFLOW PLATFORM                                    |
|                                                                                                       |
|  +---------------------------+   +---------------------------+   +---------------------------------+  |
|  |  Researcher Learning Mode |   | Protocol Version & Audits |   |  Workflow Graph Engine (DAG)    |  |
|  |  - Purpose & Rationale    |   | - Immutable SHA256 Hashes |   |  - Conditional Branching Nodes  |  |
|  |  - Common Mistake Alerts  |   | - Version Inquiries       |   |  - Verified Observation Gates   |  |
|  |  - Grounded Guidance      |   | - Report Traceability     |   |  - Recovery Transitions         |  |
|  +-------------+-------------+   +-------------+-------------+   +----------------+----------------+  |
|                |                               |                                  |                   |
|                +-------------------------------+----------------------------------+                   |
|                                                |                                                      |
|                                                v                                                      |
|  +---------------------------------------------+---------------------------------------------------+  |
|  |                   Long-Term Multi-Session Experiment Lifecycle & History                        |  |
|  |                   - Multi-day continuation ("Continue yesterday's PCR")                         |  |
|  |                   - Shift handover tokens and historical observation recall                     |  |
|  +---------------------------------------------+---------------------------------------------------+  |
|                                                |                                                      |
|                                                v                                                      |
|  +---------------------------------------------+---------------------------------------------------+  |
|  |                      Structured Laboratory Knowledge & Insight Layer                            |  |
|  |                      - Aggregated verified observations and anomaly clustering                  |  |
|  |                      - Historical context recommendations (Never overriding active protocols)   |  |
|  +-------------------------------------------------------------------------------------------------+  |
+-------------------------------------------------------------------------------------------------------+
```

---

### Area 1: Contextual Researcher Learning Mode

#### 1.1 User Pain Point
- Junior researchers and undergraduate interns often execute protocols mechanically without understanding the biochemical or physical rationale behind strict constraints (e.g., *"Why must this tube incubate at 37°C instead of room temperature?"*, *"What happens if I vortex instead of gently inverting?"*).
- Novice mistakes (such as shearing genomic DNA by vigorous vortexing or letting enzyme solutions warm up) waste expensive reagents and destroy weeks of experimental effort.

#### 1.2 User Benefit
- Researchers can ask on demand: *"이 단계는 왜 필요한가요?"* ("Why is this step necessary?"), *"이 절차의 목적이 뭐야?"* ("What is the purpose of this procedure?"), or *"주의해야 할 흔한 실수가 뭐야?"* ("What are common mistakes?").
- The agent delivers a concise, speech-friendly explanation citing approved step metadata, providing immediate clarity without removing gloves or distracting senior supervisors.

#### 1.3 Business & Commercial Value
- Dramatically cuts onboarding time and reagent wastage in academic labs, biotech startups, and contract research organizations (CROs). Serves as a major selling point for educational institutions and biotech accelerators.

#### 1.4 Technical Feasibility & Architecture Impact
- **Metadata Expansion**: Enrich `ProcedureStep` and `CuratedProtocolStep` with `purpose`, `rationale`, `scientific_principles`, and `common_mistakes`.
- **Learning Intent Route**: Add deterministic learning intent recognition in `brain.py` / `completion_intent.py` (e.g. `is_learning_question()`) and specialized grounding tools (`get_step_learning_context`).

#### 1.5 Evaluation Matrix
- **User Pain Point**: High
- **Expected User Benefit**: Very High
- **Business Value**: High
- **Technical Feasibility**: Very High
- **Architecture Impact**: Low/Medium (Non-breaking metadata extension)
- **Implementation Complexity**: Low
- **Priority**: **P0 (Immediate Core Implementation)**

---

### Area 2: Protocol Version Management & Audit Traceability

#### 2.1 User Pain Point
- In regulated laboratory settings (GLP/GMP, ISO 17025), running an unapproved or outdated revision of an SOP invalidates experimental results and leads to regulatory audit failure.
- When reviewing a completed experiment report, researchers and PIs need an immediate, indisputable answer to: *"Which protocol version and cryptographic hash was used for this run?"*

#### 2.2 User Benefit
- Direct voice inquiry support: *"현재 사용 중인 프로토콜 버전이 뭐야?"* ("What protocol version is currently active?"), *"이 실험에 사용된 프로토콜 해시 알려줘"* ("Show me the protocol hash for this experiment").
- Automatic cryptographic linkage (`protocol_sha256`, `version`, `approval_status`) in all generated Markdown, JSON, and DOCX experiment reports.

#### 2.3 Business & Commercial Value
- Prerequisite for enterprise sales in biotechnology and pharmaceutical industries. Unlocks high-margin enterprise compliance tiers.

#### 2.4 Technical Feasibility & Architecture Impact
- **Store & Projection**: Expose `protocol_version`, `protocol_sha256`, and `approval_metadata` via existing `ProcedureController` and `ExperimentReportStore`.
- **Tool Expansion**: Add `get_protocol_version_info` tool and dedicated natural voice inquiry handlers.

#### 2.5 Evaluation Matrix
- **User Pain Point**: High
- **Expected User Benefit**: High
- **Business Value**: Very High
- **Technical Feasibility**: Very High
- **Architecture Impact**: Low (Uses existing cryptographic hash columns)
- **Implementation Complexity**: Low
- **Priority**: **P0 (Immediate Core Implementation)**

---

### Area 3: Workflow Graph Architecture (DAG with Conditional Transitions)

#### 3.1 User Pain Point
- Real wet-lab protocols are non-linear:
  - *Conditional Branching*: If measured pH is < 6.8, branch to titration adjustment; otherwise proceed to digestion.
  - *Recovery Paths*: If sample pellet does not dissolve after 5 minutes, execute 2-minute ultrasonic bath before re-checking.
- Purely linear step sequences force researchers to deviate from the copilot during conditional troubleshooting.

#### 3.2 User Benefit
- Enables structured, guided handling of conditional laboratory branches and recovery paths hands-free, keeping full audit logs intact.

#### 3.3 Business & Commercial Value
- Expands coverage from ~30% of simple protocols to >85% of complex molecular biology and chemistry workflows.

#### 3.4 Technical Feasibility & Architecture Impact
- **Deterministic Server-Side Evaluation**: Graph transitions are defined in `ProcedureDefinition` with strictly typed conditions (e.g. `observation.numeric_value < 6.8`).
- **Safety Principle**: The LLM NEVER decides graph edges; the server-side state machine evaluates verified observation values against transition rules.

#### 3.5 Evaluation Matrix
- **User Pain Point**: High
- **Expected User Benefit**: Very High
- **Business Value**: High
- **Technical Feasibility**: High
- **Architecture Impact**: Medium (Extend procedure transition engine)
- **Implementation Complexity**: Medium
- **Priority**: **P1 (Immediate Core Implementation)**

---

### Area 4: Long-Term Experiment Lifecycle & Multi-Day Continuation

#### 4.1 User Pain Point
- Many biological assays require overnight incubation (e.g. 16-hour overnight protein digestion, 48-hour cell culture).
- Researchers currently cannot return the next morning and simply say: *"어제 진행하던 단백질 소화 실험 이어서 시작해줘"* ("Continue yesterday's protein digestion experiment") and resume at the exact pending step with previous batch metadata loaded.

#### 4.2 User Benefit
- Seamless cross-session and cross-day continuity; instant recall of previous step observations, batch numbers, and timestamps; zero duplicate pipetting.

#### 4.3 Business & Commercial Value
- Drives daily habit formation and user retention; establishes Voice Workflow Agent as the primary laboratory operating system.

#### 4.4 Technical Feasibility & Architecture Impact
- **Experiment Session Ledger**: Query existing SQLite `procedure_sessions` and `experiment_reports` by protocol ID and facility ID to resume the most recent incomplete session.

#### 4.5 Evaluation Matrix
- **User Pain Point**: High
- **Expected User Benefit**: Very High
- **Business Value**: High
- **Technical Feasibility**: High
- **Architecture Impact**: Low/Medium
- **Implementation Complexity**: Low/Medium
- **Priority**: **P1 (Immediate Core Implementation)**

---

### Area 5: Laboratory Knowledge Layer (Verified Observations $\rightarrow$ Institutional Insights)

#### 5.1 User Pain Point
- When unexpected phenomena occur (e.g., solution turns pale yellow or precipitate forms), junior researchers have no way of knowing if this is normal for their specific lab's water supply or reagent vendor.

#### 5.2 User Benefit
- Provides non-binding historical context: *"In 4 previous experiments in this lab, slight yellowing was observed when using Reagent Lot #B-2024 and did not affect final yield."*

#### 5.3 Business & Commercial Value
- Builds proprietary organizational data network effects for institutional lab customers.

#### 5.4 Technical Feasibility & Architecture Impact
- **Strict Grounding Boundary**: Knowledge suggestions must be marked as historical reference notes and must NEVER authorize protocol deviations or override active protocol constraints.

#### 5.5 Evaluation Matrix
- **User Pain Point**: Medium
- **Expected User Benefit**: High
- **Business Value**: Very High
- **Technical Feasibility**: Medium
- **Architecture Impact**: Medium
- **Implementation Complexity**: Medium
- **Priority**: **P2 (Immediate Foundation / Phase 3 Staged)**

---

## 3. High-Value Implementation Scope for Current Evolution

To maximize immediate user impact, commercial readiness, and architectural stability without taking on unmanageable risk, we select the following **4 core high-value capabilities** for immediate implementation:

| Module / Capability | Impact | Risk | Description |
|---|---|---|---|
| **1. Grounded Researcher Learning Mode** | Very High | Low | Enable natural voice questions about step rationale, purpose, and common mistakes with strict grounding in step metadata. |
| **2. Protocol Version & Hash Inquiries** | High | Low | Support voice queries for protocol version, hash, approval status, and report linkage with full audit traceability. |
| **3. Deterministic Workflow Graph & Branching** | Very High | Medium | Add support for conditional step transitions based on verified observation values in the procedure engine. |
| **4. Long-Term Experiment History & Continuation** | High | Low | Enable voice queries for previous experiment sessions and seamless continuation of active/pending experiments. |

---

## 4. Verification & Quality Gates

1. **Automated Unit & Integration Tests**:
   - Add new test suites covering learning mode inquiries, version queries, conditional branch transitions, and session history continuation.
2. **Regression Guarantee**:
   - Maintain 100% pass rate on all 645+ existing tests.
3. **Safety & Grounding Check**:
   - Zero hallucination in educational explanations; zero autonomous LLM state mutation.
