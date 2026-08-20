# System Architecture: Voice Workflow Agent

## 1. High-Level Architectural Overview

Voice Workflow Agent is organized into 5 primary architectural tiers:
1. **Audio & Voice Processing Pipeline** (Web Audio Worklet -> WebSocket -> WebRTC VAD -> Streaming STT -> Sentence Chunker -> Streaming TTS)
2. **AI Agent & Multi-Brain Orchestration Layer** (Intent Router -> Multi-Brain Specialists -> Grounded Tool Calling Loop)
3. **Server-Owned Workflow State Machine** (Deterministic State Controller -> Gate Checks -> SQLite Session Store)
4. **Data & Retrieval Storage Layer** (Approved Safety SQLite -> Moss Vector/BM25 In-Memory Index -> Experiment Reports Ledger)
5. **Asynchronous Safety & Handoff Subsystem** (JSONL Queue -> Worker Process -> Grok LLM Synthesis -> EML / Status API)

```
                                      +-------------------------------------------------------+
                                      |                     Browser UI                        |
                                      |   - 16kHz PCM Capture (AudioWorklet)                  |
                                      |   - Real-time Visual Cockpit & Step Cards             |
                                      |   - Synchronized Timer Countdown & Handoff Alerts     |
                                      +--------------------------+----------------------------+
                                                                 | WebSocket (/ws/audio)
                                                                 v
+------------------------------------------------------------------------------------------------------------------------------------+
|                                                  FastAPI Backend Server (server.py)                                                |
|                                                                                                                                    |
|  +------------------------------------------------------------------------------------------------------------------------------+  |
|  | [1] Voice Pipeline                                                                                                           |  |
|  |     FrameBuffer (20ms frames) ---> WebRTC VAD (vad.py) ---> xAI Whisper/REST STT ---> Normalization (language.py)           |  |
|  +-------------------------------------------------------------+----------------------------------------------------------------+  |
|                                                                | Normalized Transcript                                             |
|                                                                v                                                                   |
|  +------------------------------------------------------------------------------------------------------------------------------+  |
|  | [2] Intent Routing & Deterministic Gates                                                                                     |  |
|  |     - Emergency Recognition (emergency.py)                                                                                   |  |
|  |     - Completion Intent Classifier (completion_intent.py)                                                                    |  |
|  |     - Confirmation Gate (APPROVAL_PHRASES / CANCELLATION_PHRASES)                                                            |  |
|  +-------------------------------------------------------------+----------------------------------------------------------------+  |
|                                                                | Intent & Validated Parameters                                     |
|                                                                v                                                                   |
|  +------------------------------------------------------------------------------------------------------------------------------+  |
|  | [3] Hybrid Multi-Brain & Tool Execution Loop (brain.py / multi_brain.py / tools.py)                                         |  |
|  |     - AnswerBrain: Direct, spoken response formatting (1-3 conversational sentences)                                           |  |
|  |     - SourceBrain: Strict evidence citation & retrieval grounding                                                            |  |
|  |     - VisualBrain: PubChem / Authoritative diagram lookup                                                                    |  |
|  |     - Tool Round Controller: Max 4 rounds with context-bound schema validation                                               |  |
|  +-------------------------------------------------------------+----------------------------------------------------------------+  |
|                                                                | Tool Actions                                                      |
|                                                                v                                                                   |
|  +------------------------------------------------------------------------------------------------------------------------------+  |
|  | [4] Workflow State Machine (procedures.py)                                                                                   |  |
|  |     - States: unattached -> active(step N) -> completed | blocked_for_handoff                                                 |  |
|  |     - Gates: Explicit Verbal Command + Required Observation Verified + Fixed Timer Elapsed                                   |  |
|  +------------------------------+------------------------------+----------------------------------+-----------------------------+  |
|                                 |                              |                                  |                                |
|                                 v                              v                                  v                                |
|             +-----------------------+     +--------------------------+     +--------------------------+                            |
|             | [5] Data Tier         |     | [6] Experiment Reports   |     | [7] Reports Inbox Queue  |                            |
|             | - approved_catalog.db |     | - experiment_reports.db  |     | - reports/inbox.jsonl    |                            |
|             | - procedure_store.db  |     | - Event Ledger           |     +-------------+------------+                            |
|             | - Moss In-Memory Index|     | - JSON/DOCX/MD Exports   |                   |                                         |
|             +-----------------------+     +--------------------------+                   | (file event)                            |
+------------------------------------------------------------------------------------------|-----------------------------------------+
                                                                                           v
                                                                             +----------------------------+
                                                                             | Safety Handoff Worker      |
                                                                             | (worker.py)                |
                                                                             | - Polls inbox.jsonl        |
                                                                             | - Synthesizes Korean note  |
                                                                             | - Writes outbox/*.eml      |
                                                                             | - Updates status/*.json    |
                                                                             +----------------------------+
```

---

## 2. Voice Pipeline Architecture

### Audio Ingestion & Frame Buffering
- **Client**: Web Audio API AudioWorklet captures microphone audio at 16,000 Hz, 16-bit linear PCM mono.
- **Transport**: Binary frames over WebSocket (`/ws/audio`).
- **Server Buffer**: `FrameBuffer` aggregates incoming raw bytes into strict 20 ms frames (640 bytes per frame at 16 kHz).

### WebRTC VAD & Turn State Machine (`vad.py`)
- **VAD Algorithm**: WebRTC Voice Activity Detector operating on 20ms frames with mode 3 (aggressive filtering of lab ambient fan/fume hood noise).
- **VAD Parameters**:
  - `CASCADE_VAD_ONSET_VOICED_FRAMES` (default: 4): Number of voiced frames required to trigger speech onset.
  - `CASCADE_VAD_ONSET_WINDOW_FRAMES` (default: 6): Window size for onset evaluation.
  - `CASCADE_VAD_PREFIX_MS` (default: 300ms): Audio buffer prepended before speech onset to avoid clipping initial consonants.
  - `CASCADE_VAD_ENDPOINT_SILENCE_MS` (default: 1000ms): Continuous silence required to declare speech endpoint.
  - `CASCADE_VAD_MIN_SPEECH_MS` (default: 240ms): Filter out brief clicks or breath noises.
  - `CASCADE_VAD_MAX_UTTERANCE_MS` (default: 15000ms): Hard ceiling for single utterance turn.
- **Turn States**:
  - `IDLE`: Waiting for researcher speech.
  - `LISTENING`: Active speech detected and buffered.
  - `PROCESSING`: Endpoint reached; audio sent to STT and LLM brain.
  - `PLAYBACK`: TTS audio streaming to client.
  - `COOLDOWN`: Post-playback delay (`300ms`) preventing self-echo capture.

### Interruption (Barge-In)
- If speech onset is detected while in `PLAYBACK` state, the server issues an immediate `playback.cancel` event to the browser, drops remaining TTS frames, and switches turn state to `LISTENING`.

---

## 3. Agent & LLM Orchestration Layer

### Intent Classification & Fast Routing
1. **Emergency Gate** (`emergency.py`): Scans transcript for immediate hazards (e.g., "불이야", "화재", "폭발", "의식 잃음"). Bypasses regular tools to emit immediate stop-work and emergency contact instructions.
2. **Completion Gate** (`completion_intent.py`): Deterministically checks for step completion commands (e.g., "현재 단계를 완료했습니다", "1단계 완료", "step 2 is done"). Strictly screens against questions, negations ("아직 안 했어"), conditions, and future promises.
3. **Report Confirmation Gate** (`brain.py`): Strict allow-list matching (`APPROVAL_PHRASES`, `CANCELLATION_PHRASES`) for submitting or cancelling staged incident report drafts.

### Hybrid Multi-Brain Roles (`multi_brain.py`)
- **AnswerBrain**: Generates concise, speech-friendly Korean/English/Vietnamese response (1-3 sentences, zero markdown).
- **SourceBrain**: Validates citations and extracts verbatim facts from protocol chunks.
- **VisualBrain**: Identifies chemical entities or equipment that require visual rendering (PubChem structure, lab apparatus photo).

### Tool Chaining & Execution (`tools.py`)
- Supported tools:
  - `search_approved_safety_manual`: Queries approved safety catalog and Moss index.
  - `search_approved_lab_references`: Queries supplemental laboratory reference materials.
  - `create_safety_report`: Stages a structured incident report.
  - `check_safety_report_status`: Retrieves async worker processing state.
  - `start_procedure`: Initializes an approved procedure session.
  - `get_current_step`: Returns current step instructions and metadata.
  - `record_step_observation`: Validates and commits user-stated observation.
  - `start_step_timer`: Starts server-configured timer for the active step.
  - `complete_current_step`: Executes step transition after verifying all preconditions.
  - `get_workflow_summary`: Produces comprehensive audit trail of the session.

---

## 4. Workflow State Machine & Server Gates (`procedures.py`)

### State Lifecycle
```
[Unattached]
     |
     | start_procedure(procedure_id)
     v
[Active (Step 1)] <-----------------------+
     |                                    |
     |-- record_step_observation          | complete_current_step
     |-- start_step_timer                 | (preconditions met)
     v                                    |
[Active (Step N)] ------------------------+
     |
     |-- complete_current_step (Last step)
     |    \---> [Completed]
     |
     +-- create_safety_report + user confirmed
          \---> [Blocked for Handoff]
                     |
                     X (All mutations blocked; human supervisor required)
```

### Precondition Gates for `complete_current_step`:
1. **Explicit Verbal Authorization**: Turn transcript matches authorized completion intent.
2. **Required Observation Check**: If step definition requires observation (`observation_schema.required = true`), at least one valid observation must be recorded in SQLite.
3. **Timer Completion Check**: If step definition defines a timer duration, the server timer must have been started and the deadline timestamp must have elapsed.
4. **Handoff Block Check**: If session status is `blocked_for_handoff`, transition is rejected with `procedure_blocked_for_handoff`.

---

## 5. Asynchronous Handoff Worker (`worker.py`)

- Decoupled from the real-time voice loop to maintain sub-second voice responsiveness.
- **Queue**: Confirmed reports written to `reports/inbox.jsonl`.
- **Worker Execution**:
  1. Polls `reports/inbox.jsonl` every 2 seconds.
  2. Processes pending reports prioritized by urgency (`emergency` > `urgent` > `routine`).
  3. Invokes Grok LLM with `HANDOFF_PROMPT` to synthesize a professional Korean handoff report.
  4. Generates an `.eml` email draft in `outbox/`.
  5. Updates status file in `reports/status/<report_id>.json` (`queued_for_handoff` -> `processing` -> `handoff_ready`).
  6. Client polls status via WebSocket/REST to update UI badges automatically.
