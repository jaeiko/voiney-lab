# User Guide — Controlled Pilot

Date: 2026-08-24, revised 2026-10-08 (lane DI: one experimenter screen),
2026-10-10 (lane CL: what the 10/10 voice test used) and 2026-10-14 (lane
DOC3: long documents, re-analysis, voice level and noisy mode, phone use)

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

What the upload box takes: a **PDF**, a Word file (.docx) or a photo (JPG,
PNG, HEIC); an Excel reagent table (.xlsx) is attached to the picked protocol
in its box and can be opened there (lane UI3) -- see `SOURCE_FORMATS.md`. A
reagent table is kept beside the protocol and is not used by the analysis. A Word file needs
LibreOffice on the server (without it the server answers
`docx_conversion_unavailable`); a HEIC photo needs an optional package
(`heic_support_unavailable`; on an iPhone, "Settings → Camera → Formats → Most
Compatible" takes JPEG instead). A photo is read by OCR; a number read from
handwriting is not trusted until a person checks it against the picture.

A long document: the server reads a document too long for one analysis call
in pieces of 15 pages, joins them and checks the whole again, up to 400 pages.
The progress line says "긴 문서를 조각 … 로 나눠 분석 중입니다". If any piece
fails, the whole analysis fails and the line says which pages ("일부 조각 분석
실패 · n–m쪽"); nothing starts on a partly analysed document. In the lane AN
measurement of 2026-10-13 all three documents over 60 pages (88, 197 and 315)
ended that way, so expect a long document to need a retry or a shorter
source. Extracting the text of such a document can take two minutes or more.

Analyse again: a finished analysis can be analysed afresh with **다시 분석**.
Until the new one passes, the experiment can still start on the current one;
"다시 분석 통과 · 다음 실험부터 새 분석으로 실행합니다" says when the new one
is in. An analysis made before the analyser was improved is marked "분석기 개선
전 분석 · 다시 분석 권장". Pressing the button again while an analysis runs
does not start a second one ("이미 분석 중").

### Review an analysis

When the source-evidence check cannot confirm a value -- a quotation it cannot
find, a claim, a step number -- the server no longer throws the analysis away:
it keeps the value as an item "확인 필요" and the protocol shows "검토 대기 ·
확인 필요 n". Such an analysis does **not** run. (An analysis whose evidence
fails everywhere still just fails.) Review is optional for an analysis that
passed.

1. Open the protocol: the review view lists the items first, and beside them
   the numbered lines of the source page. A dashed line is the server's
   guess ("AI 제안"); a "?" marks a line the OCR read poorly. You can turn the
   page, and for a photo the piece of the picture a value was read from is shown.
2. For an item, only the buttons the server allows appear: confirm, correct,
   enter a value, "원문에 없음으로 비움" (leave it out), note, undo. An item
   whose quotation was not found needs the source line **you** point at before
   it can be confirmed or corrected. A safety statement cannot be edited (a
   note can be added); a step cannot be left out -- analyse again instead.
3. Write the reviewer's name and press **검토 완료하고 등록**. It is refused
   while an item is undecided. The result is review version v1 (then v2, ...),
   kept beside the analysis; the latest review version is the one that runs,
   even after a later re-analysis. Uploading the same PDF again offers the
   existing review version.
4. A value marked "검토자 입력" came from you or a colleague, "AI 제안" from
   the AI, "원문" from the source. In the experiment the voice says "검토자가
   넣은 값이에요: …" once, when the step first comes; the report lists "원문
   대비 검토자 수정 n건".

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

**Your own voice level and a noisy room (lane SP1)**

- When a session opens, the server asks you to read the sentence on the screen
  in your usual voice ("목소리를 맞출게요 …"). Its level becomes the reference
  for this session only (it is not saved). Say "건너뛰기" to skip, or "목소리
  다시 맞춰 줘" to set it again; the settings' "세션 시작 때 한 번" turns the
  request off. While it is set, a speaker clearly quieter than you -- someone
  at the next bench -- is not sent to speech recognition, is not recorded, and
  appears only as a line in the folded list "다른 사람 말로 보여 듣지 않은 말
  N개" (no words, a time only). A quiet voice does not interrupt an answer
  either.
- "목소리 민감도" (높음 / 보통 / 낮음) says how much quieter than the
  reference counts as someone else; a higher setting filters more, and earbuds
  suit the higher one.
- Settings → "주변 소리: 시끄러움" makes the server ask once before it acts on
  a state change: "3단계 완료하고 4단계로 갈까요?" or "3단계 타이머를 시작할까요?".
  Only "응/네" on the next turn does it; "아니" leaves things as they are. Notes
  are read back too ("'밴드가 흐릿해' 이렇게 기록할까요?"). In a room set to
  quiet, after three or more voices were taken for other people's within a
  minute, the server asks once "주변이 시끄러워 보여요. 시끄러운 곳 모드로
  바꿀까요?" (at most every ten minutes); it only asks, and your answer is what
  changes the mode.
- Limits: this filters by loudness only. If the room's noise is nearly as
  loud as speech, loudness cannot tell your voice from the next person's --
  use the noisy mode there. The only tests are synthetic voices and noise
  (lane SP1, 2026-10-11); nothing has been measured in a working laboratory.
- Sounds that are only fillers ("음", "어 어") are ignored without an answer.

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

### On a phone

The screen fits a phone's width and can be added to the home screen ("홈 화면에
추가", or "VoineyLab 설치" when the browser offers it; the guide is in the bar
at the top). Installing it, keeping the screen awake and using the microphone
all need an **HTTPS** address (or localhost) -- on a plain `http://` address
the guide only says "https 필요", and the microphone does not open (see
`PILOT_HTTPS_SETUP.md`, not yet carried out). No test on a real phone has been
made.

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
removed on 2026-10-10 (lane CL). The review tab (lane RV, merged 2026-10-14) is described under "Review an
analysis" above.
