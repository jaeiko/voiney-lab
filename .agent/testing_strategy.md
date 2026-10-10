# Testing Strategy & Quality Assurance

## 1. Testing Philosophy

In a wet-lab AI workflow copilot, software reliability is directly tied to experimental integrity and human safety.
Our testing pyramid spans 5 layers:
1. **Unit Tests**: Deterministic validation of intent classifiers, VAD windowing, sentence chunkers, audio converters, and schema validators.
2. **State Machine & Gate Tests**: Verification of the experiment session's step lifecycle, timer countdowns, observation validation, and completion gates.
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
.venv/bin/pytest tests/test_curated_protocol_cascade.py
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
- **Execution Rule**: An analysis carrying an execution blocker (failed source-evidence check, no executable step, a page still needing OCR, a safety-critical conflict) cannot start; the experimenter's start is the one human confirmation (`test_lane_di_execution_rule.py`). (The safety report's `blocked_for_handoff` was deleted with the report tools on 2026-10-10, lane CL.)

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

### 4. Grounded QA & Unsupported Handling Tests (`test_protocol_grounded_answers.py`, `test_answer_checks.py`, `test_lane_r6_voice_followups.py`, `test_lane_cl_kept_features.py`)
- **Supported Fact Grounding**: Questions about a protocol step are answered from its source with exact numbers and units.
- **Unsupported Query Detection**: What the source does not say is answered as such ("PDF에서 확인할 수 없어요."); substitutions and off-protocol use are not invented.
- **Outside-PDF Explanation**: A term the source does not explain may get a short general explanation after the rules' answer, labelled "AI 일반 지식"; one carrying numbers, methods or quantities is dropped, and a quantity the source answers never gets one.
- **Zero Hallucination Guarantee**: Assert that LLM responses do not invent phone numbers, legal regulations, or safety classifications.
- (The approved-reference and Moss tests left with that code on 2026-10-10, lane CL.)

### 5. Experiment Record & Report Tests (`test_experiment_reports.py`, `test_lab_report.py`)
- **Append-only Ledger**: Workflow, observation, value-confirmation and recovery events are only ever added.
- **Exports**: JSON, Markdown, CSV and DOCX carry the same record, in the researcher's time zone and plain words.
- (The safety report hand-off worker and its tests were deleted on 2026-10-10, lane CL.)
