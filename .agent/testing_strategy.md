# Testing Strategy & Quality Assurance

## 1. Testing Philosophy

In a wet-lab AI workflow copilot, software reliability is directly tied to experimental integrity and human safety.
Our testing pyramid spans 5 layers:
1. **Unit Tests**: Deterministic validation of intent classifiers, VAD windowing, sentence chunkers, audio converters, and schema validators.
2. **State Machine & Gate Tests**: Verification of procedure lifecycle, timer countdowns, observation validation, and handoff blocking.
3. **Multi-Brain & Grounding Tests**: Validation of prompt schemas, evidence extraction, citation enforcement, and unsupported fallback.
4. **WebSocket & Integration Tests**: End-to-end simulation of full voice turns, tool execution loops, barge-in cancellation, and session recovery.
5. **Frontend Regression Tests**: DOM rendering, timer widget countdowns, badge status updates, and audio worklet streaming.

---

## 2. Test Categories & Execution

### Running Tests
```bash
# Run all tests
.venv/bin/pytest -q

# Run specific domain test suites
.venv/bin/pytest tests/test_procedures.py
.venv/bin/pytest tests/test_completion_intent.py
.venv/bin/pytest tests/test_experiment_reports.py
.venv/bin/pytest tests/test_multi_brain.py
.venv/bin/pytest tests/test_vad.py
.venv/bin/pytest tests/test_candidate_a_websocket_integration.py
```

---

## 3. Required Test Suites & Verification Criteria

### 1. Workflow Completion & State Machine Tests
- **Valid Transition**: Progression from Step 1 -> Step 2 -> Step 3 -> Completed under valid conditions.
- **Missing Observation Gate**: Attempting step completion when required observation is missing must fail with `observation_required`.
- **Premature Timer Gate**: Attempting step completion before fixed timer reaches 0 must fail with `timer_not_elapsed`.
- **Observation Evidence Mismatch**: Verbatim checking ensures model cannot pass truncated or fabricated observation values (e.g., passing `A-17` when user said `A-170` must fail with `observation_evidence_mismatch`).
- **Handoff Block Enforcement**: When a safety report is submitted, session transitions to `blocked_for_handoff`; subsequent step completions, timer starts, or observations must be rejected.

### 2. Intent Classification & Guardrail Tests (`test_completion_intent.py`)
- **Positive Current Step Completion**: "현재 단계를 완료했습니다", "이 단계 완료했어요", "다 했어", "여기까지 마쳤어".
- **Positive Explicit Numbered Completion**: "1단계 완료", "step 2 is done", "삼단계 다 했어".
- **Negative & Question Rejection**:
  - Questions: "완료 기준이 뭐야?", "다 끝난 건가요?", "완료해야 하나요?".
  - Negations: "아직 완료 못했어", "끝내지 않았습니다".
  - Future/Hypothetical: "완료할 예정이야", "끝나면 알려줘", "완료했다고 치면".

### 3. Voice Activity Detection & Turn-Taking Tests (`test_vad.py`)
- **Silence & Noise Immunity**: Lab fume hood and background fan noise must not trigger false speech onsets.
- **Prefix Buffering**: Ensure initial unvoiced consonants (e.g. "p", "t", "s") are preserved in prefix buffer.
- **Endpoint Detection**: Verification of silence threshold before triggering turn completion.
- **Barge-In Interruption**: Simulating incoming audio while in `PLAYBACK` must immediately trigger `playback.cancel` and abort pending TTS chunks.

### 4. Grounded QA & Unsupported Handling Tests (`test_approved_references.py`, `test_moss_retrieval.py`)
- **Supported Fact Grounding**: Queries about approved SOP steps return exact citations and accurate numerical quantities.
- **Unsupported Query Detection**: Out-of-catalog questions (e.g., asking for unapproved reagent substitutions or off-label equipment use) return explicit unsupported messages directing the user to the lab supervisor.
- **Zero Hallucination Guarantee**: Assert that LLM responses do not invent phone numbers, legal regulations, or safety classifications.

### 5. Asynchronous Handoff & Worker Tests (`test_worker.py`, `test_experiment_reports.py`)
- **Queue Persistence**: Reports placed in `inbox.jsonl` are atomically consumed and moved to `processed.txt`.
- **Handoff Artifacts**: `.eml` files generated with correct subject, urgent prefix, location, and structured workflow snapshot.
- **Retry Mechanism**: Worker retries failed LLM generation up to 3 times before marking `retry_pending` / `failed`.
