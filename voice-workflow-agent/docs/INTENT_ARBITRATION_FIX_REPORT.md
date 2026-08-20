# Intent Arbitration Fix Report: Server-Owned Priority & Laboratory Reliability

**Document**: `docs/INTENT_ARBITRATION_FIX_REPORT.md`  
**Author**: Principal AI Engineer, AI Agent Architect & Startup CTO  
**Repository**: `/home/student/voice-ai-course/voice-workflow-agent`  
**Branch**: `refactor/voice-workflow-agent-stability`  
**Date**: August 20, 2026  
**Status**: **ALL INTENTS ROUTED, TEST SUITE PASSING (100%), AX ARCHITECTURE VERIFIED**  

---

## 1. Executive Summary

During live voice testing with laboratory researchers, specialized user intents (Learning Mode, Protocol Audit, Experiment Resume, Compound Inquiries, and Speculative Uncertainty) were occasionally overridden by generic procedural explanations.

This issue did not stem from an architectural flaw in server-owned state management or curated protocol fixtures. Rather, the server-side intent arbitration lacked granular priority classification and particle/conjugation coverage for Korean spoken colloquialisms.

In this update, we have implemented a strict **8-tier deterministic intent arbitration hierarchy** that evaluates specialized intents *before* generic procedural guidance, while strictly upholding the core AX principle: **the server owns 100% of workflow transitions and tool execution authorizations; the LLM only formats language responses without controlling state**.

---

## 2. Root Cause Analysis & Resolved Failures

```
+-------------------------------------------------------------------------------------------------------------------------------+
| Failure Case                        Root Cause                              Applied Fix & Outcome                             |
|-----------------------------------  --------------------------------------  ------------------------------------------------  |
| **Failure 1: Learning Question**    Regex omitted short forms (*"왜 해야    Broadened `is_learning_question()` to capture all |
| *"왜 해야 돼?"*, *"이 단계를 왜...*  돼?"*, *"왜 필요한가?"*).              colloquial forms. Returned purpose, rationale, &  |
|                                                                             rookie mistakes without reciting procedure text.  |
|                                                                                                                               |
| **Failure 2: Audit Question**       Postpositional particles (*"의"*,       Expanded `is_version_question()` to capture       |
| *"현재 프로토콜의 버전을 알려줘"*   *"을"*) broke exact regex matching.     particle variations. Returned title, version, doc |
|                                                                             version, and SHA256 checksum.                     |
|                                                                                                                               |
| **Failure 3: Resume Safety**        Missing imperative conjugations         Expanded `is_history_or_continuation_intent()`.   |
| *"어제 실험 이어줘"* (no session)   (*"이어줘"*); returned vague prompt.    Returns exact safe message:                       |
|                                                                             *"저장된 진행 중인 실험 세션을 찾지 못했습니다."* |
|                                                                                                                               |
| **Failure 4: Speculative Outcome**  Ungrounded speculation (*"성공할까?"*)  Added `is_speculative_or_uncertainty_question()`. |
| *"이 실험 결과가 성공할까?"*        fell through to generic procedure text. Returns safe uncertainty guidance: *"현재 정보만으로   |
|                                                                             실험 성공 여부를 판단할 수 없습니다..."*         |
|                                                                                                                               |
| **Failure 5: Combined Question**    Compound query confused the router      Added `is_combined_learning_and_next_question()`. |
| *"왜 하는지 알려주고 다음 단계..."* between learning and transition.       Explains current, previews next, asks confirm     |
|                                                                             without auto-advancing.                           |
+-------------------------------------------------------------------------------------------------------------------------------+
```

---

## 3. Server-Side Intent Arbitration Priority Hierarchy

```
Researcher Audio Input
          │
          ▼
[ STT & Transcription Normalization ]
          │
          ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        DETERMINISTIC INTENT ARBITRATION GATES                          │
│                                                                                        │
│  1. Emergency & Safety Intent      ──► Recognize immediate danger & halt               │
│  2. Explicit Workflow Control      ──► Advance step (e.g. "1단계 완료", Start/Stop)    │
│  3. Speculative Uncertainty Intent ──► Safe non-hallucinatory uncertainty response     │
│  4. Combined Learning + Next Step  ──► Explain current + preview next + ask confirm    │
│  5. Step Learning Intent           ──► Return biochemical purpose, rationale, mistakes │
│  6. Protocol Audit Intent          ──► Return SOP title, version, & SHA256 hash        │
│  7. Experiment Resume / History    ──► Resume prior run OR "저장된 세션 없음"          │
│  8. Generic Procedure Elaboration  ──► Current step recitation or grounded manual QA   │
└────────────────────────────────────────────────────────────────────────────────────────┘
          │
          ▼
[ Response Formatter (Professor Persona, Leo Voice) ]
```

---

## 4. Persona & Prompt Alignment

- **Professor Persona**: Defined in `SYSTEM_PROMPT` in [`src/voice_workflow_agent/brain.py`](file:///home/student/voice-ai-course/voice-workflow-agent/src/voice_workflow_agent/brain.py):
  > *"You embody the Professor persona: a calm, professional, and supportive laboratory mentor. Your explanations are educational, precise, and encouraging, focusing on safety, scientific principles, and experimental reproducibility without excessive verbosity."*
- **Voice Profile**: **Leo (`TTS_VOICE=leo`)** across all synthesis pipelines.

---

## 5. Verification & Test Metrics

### Test Suite Execution
- **Priority Test Suite ([`tests/test_intent_arbitration_priority.py`](file:///home/student/voice-ai-course/voice-workflow-agent/tests/test_intent_arbitration_priority.py))**: 5/5 PASSED (100%).
- **Scenario Test Suite ([`tests/test_voice_product_scenarios.py`](file:///home/student/voice-ai-course/voice-workflow-agent/tests/test_voice_product_scenarios.py))**: 4/4 PASSED (100%).
- **Product Evolution Suite ([`tests/test_product_evolution.py`](file:///home/student/voice-ai-course/voice-workflow-agent/tests/test_product_evolution.py))**: 11/11 PASSED (100%).
- **Full Test Suite (`pytest -q`)**: **665 passed, 0 failures, 674 subtests passed**.

---

## 6. Preserved State Machine Invariants

1. **Server Authority**: No LLM token generation or tool call can mutate workflow state or transition steps without explicit server state machine execution.
2. **Zero Autonomous Branching**: The agent never advances to the next step during combined or learning inquiries without explicit researcher confirmation.
3. **Fail-Closed Persistence**: Session resumes require verifiable session IDs in SQLite; non-existent sessions trigger safe fallback disclosures.
