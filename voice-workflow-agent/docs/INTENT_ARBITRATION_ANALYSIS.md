# Intent Arbitration Analysis: Server-Owned Routing Priority & Laboratory Reliability

**Document**: `docs/INTENT_ARBITRATION_ANALYSIS.md`  
**Author**: Principal AI Engineer, AI Agent Architect & Startup CTO  
**Repository**: `/home/student/voice-ai-course/voice-workflow-agent`  
**Branch**: `refactor/voice-workflow-agent-stability`  
**Date**: August 20, 2026  
**Status**: **DIAGNOSTIC ARCHITECTURE AUDIT**  

---

## 1. Execution Path & Runtime Routing Architecture

The Voice Workflow Agent processes spoken researcher audio through a strictly server-controlled pipeline:

```
Researcher Voice Audio (PCM 16kHz)
            │
            ▼
[ server.py / WebSocket Handler ]
            │
            ▼
[ STT & Transcription Admission Engine ] (Whisper / Local Model)
            │  (Korean phonetic normalization & keyterm admission)
            ▼
[ Server-Side Intent Arbitration Gate ]
            │
            ├─► Priority 1: Emergency & Laboratory Safety (`recognize_emergency`)
            ├─► Priority 2: Explicit Workflow Control (`resolve_korean_completion_decision`, Start/Stop)
            ├─► Priority 3: Combined Learning + Next Step Preview (`is_combined_learning_and_next_question`)
            ├─► Priority 4: Step Learning Context (`is_learning_question`)
            ├─► Priority 5: Protocol Version & Cryptographic Audit (`is_version_question`)
            ├─► Priority 6: Multi-Session Continuation & History (`is_history_or_continuation_intent`)
            ├─► Priority 7: Speculative Scientific Uncertainty (`is_speculative_or_uncertainty_question`)
            └─► Priority 8: Generic Procedure Elaboration / Fallback Grounded QA
            │
            ▼
[ Brain Dispatcher & Tool Context ] (`stream_brain_turn` / `ProcedureController`)
            │
            ▼
[ Response Formatter & Persona Filter ] (Professor Persona, Leo Voice TTS)
            │
            ▼
Researcher Speaker Audio (PCM Stream)
```

---

## 2. Component Responsibility Matrix

| Component | Responsibility | Constraints |
|---|---|---|
| **`server.py`** | WebSocket lifecycle, STT coordination, emergency intercept, audio streaming. | Must never mutate state without server engine authorization. |
| **`completion_intent.py`** | Deterministic intent classification (Emergency, Completion, Learning, Version, Resume, Speculation). | Must evaluate specific intents before generic patterns. |
| **`procedures.py`** | State machine transitions, timer enforcement, observation validation, SQLite persistence. | Exclusive owner of workflow progression. |
| **`brain.py`** | Fast-path dispatch for deterministic queries, Professor persona formatting, tool execution. | Zero autonomous transition generation. |
| **`curated_protocol.py`** | Offline development fixture playback (Candidate A reference baseline). | Self-contained, isolated from operational SQLite store. |

---

## 3. Root Cause Analysis of Voice Failures

### 3.1 Failure 1: Learning Question Sub-Patterns
- **Observed**: Utterances like *"왜 해야 돼?"*, *"이 단계를 왜 해야 돼?"*, *"왜 필요한가?"* occasionally defaulted to generic procedure explanations.
- **Root Cause**: When a question contained auxiliary colloquial forms (*"왜 해야 돼"*, *"왜 하는 거야"*, *"이 단계를 왜"*) without the exact keyword *"단계는"*, token boundary variations allowed generic fallback.

### 3.2 Failure 2: Protocol Version Particle Interception
- **Observed**: *"현재 프로토콜의 버전을 알려줘"* returned current step instructions instead of Audit Mode.
- **Root Cause**: Regex in `is_version_question` looked for `r"프로토콜\s*버전"` and `r"버전\s*알려"`. The presence of Korean postpositional particles `"의"` and `"을"` (*"프로토콜의 버전을"*) broke exact regex matching, allowing the query to fall through to the LLM.

### 3.3 Failure 3: Experiment Resume Conjugation & Safety
- **Observed**: *"어제 실험 이어줘"* and *"어제 실험을 이어줘"* failed to resume, and when no session was stored, fallback responses were vague.
- **Root Cause**:
  1. Regex had `r"이어서"` but missed imperative conjugations `r"이어줘"`, `r"이어줘요"`, `r"이어주세요"`.
  2. Fallback response when no previous session was found lacked the exact required safety message: *"저장된 진행 중인 실험 세션을 찾지 못했습니다."*

### 3.4 Failure 4: Speculative Outcome Questions
- **Observed**: *"이 실험 결과가 성공할까?"* returned procedure instructions.
- **Root Cause**: No dedicated server intent handled speculative outcome questions. Without deterministic interception, the LLM attempted to explain the current step instead of issuing a safe uncertainty disclosure.

### 3.5 Failure 5: Combined Question Handling
- **Observed**: *"이 단계 왜 하는지 알려주고 다음 단계도 알려줘"* confused the router.
- **Root Cause**: Lacked compound intent arbitration to explain the current step's purpose, preview the next step, and explicitly ask for confirmation before transitioning.

---

## 4. Target Deterministic Priority Design

```
+-------------------------------------------------------------------------------------------------------+
| Priority Level | Intent Type                           | Handler Action                               |
|:--------------:|:--------------------------------------|:---------------------------------------------|
| **1**          | Emergency / Laboratory Danger         | Immediate emergency instructions & halt       |
| **2**          | Explicit Workflow Control (Complete)  | Advance step via `controller.complete()`      |
| **3**          | Combined Learning + Next Preview      | Explain purpose + preview next + confirm      |
| **4**          | Learning Mode (Why / Purpose / Traps) | Return purpose, rationale, & mistakes        |
| **5**          | Protocol Audit (Version & Hash)       | Return SOP ver, doc ver, and SHA256 checksum  |
| **6**          | Experiment Resume / History           | Load session or return "저장된 세션 없음"     |
| **7**          | Speculative Uncertainty (Success?)    | Safe uncertainty response                     |
| **8**          | Generic Procedure Guidance / Fallback | Grounded instruction or manual search QA      |
+-------------------------------------------------------------------------------------------------------+
```
