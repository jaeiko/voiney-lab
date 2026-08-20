# Voice Routing Failure Analysis: Conversational Routing Breakdown & Diagnosis

**Document**: `docs/VOICE_ROUTING_FAILURE_ANALYSIS.md`  
**Author**: Principal AI Engineer, AI Agent Architect & Startup CTO  
**Repository**: `/home/student/voice-ai-course/voice-workflow-agent`  
**Date**: August 20, 2026  
**Status**: **ROOT CAUSES IDENTIFIED & ARCHITECTURAL FIX PLANNED**  

---

## 1. Executive Summary

During hands-free laboratory voice testing of the **Voice Workflow Agent**, researchers encountered a critical disconnect between the platform's backend capabilities and its conversational routing layer. 

Although the underlying backend modules for **Researcher Learning Mode** (`get_step_learning_context`), **Protocol Version Management** (`get_protocol_version_info`), **Workflow DAG Branching**, and **Multi-Session Continuation** (`continue_experiment`, `get_experiment_history`) were fully operational and passed isolated unit tests, spoken user queries failed to activate these capabilities. Instead, the agent repeatedly re-read the active procedure step instructions.

This document presents a root-cause diagnosis across the entire pipeline:
$$\text{Voice Input} \longrightarrow \text{STT} \longrightarrow \text{Intent Classification} \longrightarrow \text{Brain Routing} \longrightarrow \text{Tool Selection} \longrightarrow \text{Response Generation}$$

---

## 2. Analysis of Real Voice Failure Scenarios

```
+-----------------------------------------------------------------------------------------------------------------------+
|                                              VOICE FAILURE DIAGNOSTIC TRACE                                           |
|                                                                                                                       |
|  [ Researcher Voice ]                                                                                                 |
|         |                                                                                                             |
|         v                                                                                                             |
|  [ STT Transcript ] -----------------> "근데 이 단계를 왜 해야 되는데?" / "현재 프로토콜 버전 알려줘" / "어제 하던 실험 이어서 해줘"|
|         |                                                                                                             |
|         +---> [ Curated Protocol Intent Classifier ]                                                                  |
|         |           | (No specialized learning/version/resume patterns)                                               |
|         |           v                                                                                                 |
|         |         Mapped to: `FULL_DETAIL` or `CURRENT` or `OFF_TOPIC`                                                |
|         |           v                                                                                                 |
|         |         Response: Re-reads `fixture.steps[current_index].instruction` (FAILURE)                             |
|         |                                                                                                             |
|         +---> [ Brain Turn Loop (Grok) ]                                                                              |
|                     | (Grok receives `tools=TOOLS` [9 base tools only; extended tools omitted])                       |
|                     v                                                                                                 |
|                   Grok cannot call `get_step_learning_context` or `get_protocol_version_info`                         |
|                     v                                                                                                 |
|                   Grok calls `get_current_step` and re-reads step text (FAILURE)                                      |
+-----------------------------------------------------------------------------------------------------------------------+
```

### Scenario 1: Researcher Learning Question
- **User Utterance**: *"근데 이 단계를 왜 해야 되는데?"* ("Why do I even have to do this step?")
- **Expected Outcome**: Spoken explanation of the biochemical purpose, scientific rationale, and common mistakes of the current step from approved SOP metadata.
- **Actual Outcome**: The agent repeated the step instruction: *"1단계: 용액 A 500 마이크로리터를 튜브에 분주하세요."*
- **Diagnostic Breakdown**:
  1. *STT*: Correctly transcribed `"근데 이 단계를 왜 해야 되는데?"`.
  2. *Intent Classification*: The conversational connector `"근데"` and colloquial ending `"~해야 되는데?"` were not matched by the previous basic regex in `completion_intent.py`. In `curated_protocol.py`, `"단계를 ..."` matched `_STEP_ELABORATION_PATTERNS`, which triggers `CuratedProtocolAction.FULL_DETAIL` (reading the step instruction in full).
  3. *Brain Routing & Tool Selection*: In `stream_brain_turn`, the tool array passed to Grok (`tools=TOOLS`) contained only the 9 original base tools. `get_step_learning_context` was missing from `TOOLS`.
  4. *Response Generation*: Grok fell back to calling `get_current_step` or quoting the instruction.

### Scenario 2: Protocol Version & Audit Inquiry
- **User Utterance**: *"현재 프로토콜 버전 알려줘."* ("Tell me the current protocol version.")
- **Expected Outcome**: Spoken confirmation of the protocol title, active version, document version, and cryptographic SHA256 checksum.
- **Actual Outcome**: The agent repeated the current procedure step description.
- **Diagnostic Breakdown**:
  1. *STT*: Correctly transcribed `"현재 프로토콜 버전 알려줘."`.
  2. *Intent Classification*: `curated_protocol.py` classified `"현재 프로토콜 ..."` as `CURRENT` step request.
  3. *Brain Routing & Tool Selection*: Grok was provided only `TOOLS`, which lacked `get_protocol_version_info`.
  4. *Response Generation*: Grok called `get_current_step` and spoke the current step instruction.

### Scenario 3: Long-Term Experiment Continuation
- **User Utterance**: *"어제 하던 실험 이어서 해줘."* ("Continue yesterday's experiment.")
- **Expected Outcome**: Retrieve the previous session, identify the protocol and completed steps, resume the session, and confirm before proceeding.
- **Actual Outcome**: The agent repeated the current procedure step.
- **Diagnostic Breakdown**:
  1. *STT*: Correctly transcribed `"어제 하던 실험 이어서 해줘."`.
  2. *Intent Classification*: The phrase `"이어서 해줘"` was matched by generic workflow start/continuation patterns (`_WORKFLOW_COMMANDS` / `_START_COMMAND_PATTERNS`) which simply repeat or start the local active fixture.
  3. *Brain Routing & Tool Selection*: Neither `get_experiment_history` nor `continue_experiment` were in Grok's tool definitions or fast deterministic router.

---

## 3. Root Cause Summary

| # | Root Cause | Affected Component | Impact |
|---|---|---|---|
| **RC-1** | **Extended Tools Omitted from LLM Toolset** | `src/voice_workflow_agent/brain.py` (`stream_brain_turn`) | Grok was only given `TOOLS` (9 tools). `EXTENDED_PROCEDURE_TOOLS` were never exposed to OpenAI function calling. |
| **RC-2** | **Lack of Fast Deterministic Informational Routing** | `src/voice_workflow_agent/brain.py` | Informational inquiries (learning, version, history) were forced through nondeterministic LLM loops rather than sub-millisecond deterministic handlers. |
| **RC-3** | **Curated Protocol Classifier Misrouting** | `src/voice_workflow_agent/curated_protocol.py` | `classify_curated_control_intent` lacked action types for `LEARNING_CONTEXT`, `PROTOCOL_VERSION`, and `CONTINUATION`, misclassifying them as `FULL_DETAIL` or `CURRENT`. |
| **RC-4** | **Colloquial Korean Phrase Gaps** | `src/voice_workflow_agent/completion_intent.py` | Intent classifiers failed on common conversational prefixes (`근데`, `있잖아`, `전에`, `지난`) and endings (`~되는데?`, `~해야 돼?`, `~알려줘`). |
| **RC-5** | **Persona & Tone Ambiguity** | `src/voice_workflow_agent/server.py`, `brain.py` | Default voice was `lux` rather than `leo`, and response templates lacked clear mode separation (Instruction vs Learning vs Audit vs Resume). |

---

## 4. Architectural Fix Strategy

```
+-----------------------------------------------------------------------------------------------------------------------+
|                                              REVISED DUAL-ROUTING ARCHITECTURE                                        |
|                                                                                                                       |
|  [ User Spoken Transcript ]                                                                                           |
|         |                                                                                                             |
|         +---> [ Fast Deterministic Intent Classifier ]                                                                |
|         |           |                                                                                                 |
|         |           +---> is_learning_question()  ==> Direct execute: `get_step_learning_context`                     |
|         |           +---> is_version_question()   ==> Direct execute: `get_protocol_version_info`                     |
|         |           +---> is_history_or_continuation_intent() ==> Direct execute: `continue_experiment`               |
|         |           |                                                                                                 |
|         |           v (Deterministic Format in Professor Persona: < 15ms latency, 0% hallucination)                   |
|         |         [ Formatted Spoken Response ]                                                                       |
|         |                                                                                                             |
|         +---> [ Curated Protocol Mode ]                                                                               |
|         |           v                                                                                                 |
|         |         `CuratedProtocolAction.LEARNING_CONTEXT`, `PROTOCOL_VERSION`, `CONTINUATION`                        |
|         |           v                                                                                                 |
|         |         [ Formatted Spoken Response ]                                                                       |
|         |                                                                                                             |
|         +---> [ General LLM Brain Turn ]                                                                              |
|                     v                                                                                                 |
|                   `tools = TOOLS + EXTENDED_PROCEDURE_TOOLS`                                                          |
|                     v                                                                                                 |
|                   Grok has full function-calling schemas for all 13 tools                                             |
+-----------------------------------------------------------------------------------------------------------------------+
```

### 1. Unified Fast-Path Intent Routing in `brain.py`
In `stream_brain_turn`, before invoking the LLM tool loop:
- Intercept learning questions (`is_learning_question`), version questions (`is_version_question`), and continuation intents (`is_history_or_continuation_intent`).
- Execute the controller method directly.
- Format concise, spoken Korean/English responses following the **Professor Persona**.
- Stream directly to `on_sentence` and return `BrainResult` with 0ms tool wait.

### 2. Comprehensive Tool Availability in LLM Calls
When falling through to Grok:
- Supply `tools = TOOLS + EXTENDED_PROCEDURE_TOOLS` whenever `tool_context` is present.

### 3. Curated Protocol State Machine Extension
- Add `LEARNING_CONTEXT`, `PROTOCOL_VERSION`, and `CONTINUATION` to `CuratedProtocolAction`.
- Enhance `classify_curated_control_intent` in `curated_protocol.py` to route learning/version/continuation requests before falling back to step elaboration.

### 4. Persona & Tone Upgrade
- Configure default `TTS_VOICE` to `leo`.
- Implement distinct voice response formatting:
  - **Instruction Mode**: *"다음 단계는 [단계명]입니다. [지시사항]을 진행해 주세요."*
  - **Learning Mode**: *"이 단계의 목적은 [목적]입니다. [원리/이유] 때문이며, [주의할 점]에 유의해야 합니다."*
  - **Audit Mode**: *"현재 프로토콜은 [프로토콜명] 버전 [버전], 문서 버전은 [문서버전]이며, SHA256 해시는 [앞8자리]입니다."*
  - **Resume Mode**: *"이전 실험 상태를 확인했습니다. [프로토콜명]의 [N]단계까지 완료된 세션을 불러왔습니다. 계속 진행할까요?"*

---

## 5. Required Regression Tests

1. `tests/test_voice_product_scenarios.py`:
   - Test Learning Question routing with colloquial Korean variations (*"근데 이 단계를 왜 해야 되는데?"*, *"왜 이 단계 해야 해?"*, *"이 단계 목적이 뭐야?"*, *"주의할 점 알려줘"*).
   - Test Protocol Version inquiry routing (*"현재 프로토콜 버전 알려줘"*, *"이 SOP 몇 버전이야?"*, *"이 문서 해시 알려줘"*).
   - Test Experiment Continuation routing (*"어제 하던 실험 이어서 해줘"*, *"전에 하던 것 계속하자"*, *"지난 실험 불러줘"*).
   - Test Normal procedure progression (*"시작해줘"*, *"다음 단계로 가자"*).
2. Existing test suites: Full pass of all 656 pytest tests with zero regressions.
