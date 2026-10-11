# User Guide — Controlled Pilot

Date: 2026-08-24, revised 2026-10-08 (lane DI: one experimenter screen) and
2026-10-10 (lane CL: what the 10/10 voice test used)

VoineyLab is an MVP prototype, not field-validated.

This guide covers the experimenter's screen, the only one since 2026-10-08.
It does not replace a laboratory SOP, safety training, or emergency
procedure. Stop and follow facility policy whenever the protocol and the
physical situation differ.

## Experimenter

### Upload a protocol

1. Upload the protocol PDF. The server reads it, runs OCR by itself for pages
   without a text layer, and analyses it. Nothing is asked of you until the
   analysis is done.
2. When the analysis is in, the start screen ("시작 전 확인 · 분석 결과
   요약") shows the step count, every safety statement the source declares
   beside its Korean, "실행을 막는 사유" with what to do, and "시작 전 알림"
   for things the guidance cannot handle yet (parallel work, a recurring
   step, a reusable subprocedure -- those are said once at their step and
   the source is read).
3. A blocked analysis (an unreadable page, a failed source-evidence check, no
   executable step, a safety-critical conflict) has to be fixed at the
   source. Do not work around the block.

### Start an experiment

1. Read the safety statements. Pressing **이 프로토콜로 시작** is the one
   human confirmation; it is written to the experiment record as "안전 주의
   확인".
2. Before starting, verify the context card shows the expected protocol,
   "분석 통과 · 실행 가능", the current step and the available actions.
3. If an open experiment is explicitly selected, the action changes to
   **Resume experiment** and shows what will and will not be restored.
4. Start the voice session. The visible voice states mean Ready, Listening,
   Understanding request, and Providing guidance.

### Work hands-free

Useful commands include:

- “Start the protocol.”
- “What is the current step?”
- “Why do I do this step?”
- “What are the warnings?”
- “Start the step timer.”
- “I completed this step.”
- “Pause the workflow.”

What the 2026-10-10 voice test used, as it works now:

- **Timers** — the step timer tells you one minute before its end and at its
  end, on the screen and aloud (after you stop talking, if you are). "몇 분
  남았어?" / "언제 끝나?" are answered from the timer; with none running,
  "지금 도는 타이머는 없어요."
- **"그거"** — "그거 얼마나 넣어?" and the like are matched to the current
  step's own values.
- **Questions** — what the source says is answered from the source. A term
  the source does not explain ("HPLC water 가 뭐야?") may get a short general
  explanation after that answer, said as "PDF에는 따로 설명이 없어요. 일반적으로는
  …" and shown as "AI 일반 지식" (when the outside-PDF explanation is on). It
  never carries values, methods or safety guidance.
- **Pictures** — "그림 보여줘" shows the source's figure of the step, with
  "그림 크게 보기" and "원본 쪽 보기". When the source has none and drawing is
  on, a drawing is made at once and marked "AI 가 그린 그림". With the web
  explanation on, "○○ 사진 보여줘" looks the thing up on the web, with sources.
- **Notes** — "침전물 안 보임 남겨 줘" records what you said; a sentence with a
  negation is kept whole.

A completion request may ask for explicit confirmation. Confirm only after the
physical work is actually complete. Explanations, warnings, protocol audits,
history, and previews must not change the step.

If recognition is empty, non-speech, language-inconsistent, or ambiguous, the
system keeps the current step and asks for another request. Check the current
step before retrying any state-changing command.

### Record observations and evidence

- Add a voice or manual observation to the current step. It is labeled
  observation-only and cannot modify the protocol's instructions.
- A value the protocol needs ("몇 번 반복하셨나요?") is asked and the answer
  recorded; the report lists the values to confirm.
- Attach a JPEG, PNG, WebP, PDF, or DOCX up to 32 MiB. It is stored as
  not-interpreted evidence.
- Use **Download original evidence** in the timeline. A missing, changed, or
  invalid stored object is refused rather than returned unchecked.

### Pause, refresh, and resume

1. Pause before intentionally leaving the bench session.
2. After a refresh or reconnect, select the exact open experiment.
3. Read the recovery disclosure. The server restores the protocol/revision,
   contiguous completed steps, and current step.
4. Pending confirmations, prior conversation, and active timers are not restored.
   Re-establish those intentionally if needed.
5. If the UI reports stale state, refresh the timeline and reselect the session;
   do not assume the last command succeeded.

## End a pilot session

1. Confirm the final experiment status and timeline.
2. Export the required report format. CSV is available for structured transfer;
   JSON/Markdown/DOCX are also supported. The report's "프로토콜 상태" reads
   "분석 통과 · 실험자가 시작함".
3. Ask the operator to create and verify the post-session backup.
4. Record issues using the incident template in `PILOT_READINESS_PACKAGE.md`.

The reviewer and laboratory-administrator screens, protocol approval, lab
adaptations and the eLabFTW export were removed on 2026-10-08 (lane DI). The
voice answer from approved lab references and the voice safety report were
removed on 2026-10-10 (lane CL). A review tab, where a person reviews the
analysis before it is registered, is planned (lane RV); it is not in this
build.
