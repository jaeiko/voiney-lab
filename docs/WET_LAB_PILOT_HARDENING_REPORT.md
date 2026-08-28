# Wet-lab pilot hardening — implementation and validation status

Status after this pass: **Controlled Pilot Ready — Engineering / Field-Unvalidated.**

That label is unchanged on purpose. This pass closed engineering gaps that made
the product fragile in a shared laboratory; it did not put the product in a
laboratory. Nothing below may be re-described as production readiness or as
real-lab noise robustness until real researchers provide evidence.

The read-only audit this work started from is in
[`NOISY_LAB_VOICE_DIAGNOSIS.md`](NOISY_LAB_VOICE_DIAGNOSIS.md).

---

## 1. Implemented and covered by tests in this repository

### Noise-aware interruption gate (`barge_in.py`)

Barge-in is now decided by two independent things that must agree:

| Question | Answered by |
| --- | --- |
| Is this speech-shaped, for long enough? | `vad.py` endpoint detector (unchanged) |
| Is it loud enough *for this room*, sustained, and clear of the playback-onset echo window? | `barge_in.py` interruption gate (new) |

* **Adaptive ambient floor.** A minimum-statistics estimator — follow the level
  down quickly, creep up slowly — replaces the previous single fixed threshold.
  It needs no voice-activity verdict of its own, which matters: gating floor
  updates on "not speech" is circular, because in a continuously loud room no
  frame is ever quiet enough to update the floor.
* **Playback-onset cooldown** (120 ms) covers the agent's own speech returning
  through the room when browser echo cancellation is weak. Short on purpose: the
  detector's own 240 ms sustained-voiced requirement covers everything after it,
  so a longer cooldown would delay every legitimate barge-in to close a hole
  that is already closed.
* **A dismissed candidate is not a failed turn.** Playback continues, no
  `barge_in_candidate` reaches the browser, no phantom user command enters the
  voice history, and canonical workflow state is untouched.
* **Fixed hang.** `playback_ended` previously refused while *any* interruption
  candidate was outstanding, so sustained equipment hum could pin a session in
  `AGENT_SPEAKING` after the answer had finished. Only an *announced* candidate
  defers playback completion now.
* **Priority stop fast path.** A short explicit `멈춰 / 그만 / 잠깐 / 일시정지 /
  stop / pause` is matched by an anchored deterministic pattern and confirms an
  interruption immediately. It is anchored so a stop word inside a longer
  sentence — "그만두지 말고 계속 알려줘" — never halts the agent.

Every threshold is bounded, validated, environment-overridable, and documented
next to the reason it exists in `.env.example` and in the module.

### Playback, workflow and persistence are three separate facts

The server already kept these apart; the UI did not. A superseded turn was
labelled 중단됨 with no surviving statement of what it had actually done.

* the server publishes `turn.outcome` after **both** persistence gates, carrying
  the workflow outcome, whether the auxiliary experiment report persisted, and
  whether canonical workflow state persisted;
* `turn.route_decision` no longer claims anything was saved — it states intent,
  and the card reads 처리 중… until the outcome settles;
* interrupting playback writes a separate 음성 재생 row and leaves 처리 결과
  untouched;
* a cancelled turn now reads "답변 재생이 중단됐습니다. 저장된 실험 상태는
  그대로 유지됩니다." instead of "중단됨".

### Korean-first presentation of an English approved source (`source_presentation.py`)

One boundary, deliberately not sprinkled through mutation code:

1. a reviewer-approved Korean sidecar always wins and is the only thing allowed
   to be called 검증된 한국어 번역;
2. otherwise, when explicitly enabled, a runtime translation may be generated
   through the existing model boundary and is **mechanically checked** —
   every number, unit, concentration, duration and identifier in the source must
   survive, *counted*, not merely present, so a source that states 100 mM twice
   and a translation that states it once is rejected;
3. otherwise the exact approved source is the answer, with an honest notice.

A runtime translation is always labelled 자동 번역. An unresolved execution gate
is treated as safety-critical and never reaches a translator at all. The exact
approved text always stays available under 원문 보기.

The concrete gap this closes: "다음 단계 알려줘" previously embedded raw English
in a Korean sentence whenever a step had no approved sidecar, with no indication
that it was doing so.

### Participant-aware sessions (`speaker_attribution.py`)

Optional, **off by default**, consumed through the documented `diarize` field of
the batch transcription endpoint this repository already calls.

* provider word objects become provider-neutral `TranscriptSegment`s; no raw
  provider event shape reaches workflow state;
* the participant roster is seeded server-side from the authenticated principal,
  never from anything the browser asserts and never from the audio;
* a diarized label is associated with a participant only by an explicit human
  confirmation (`session.speaker.confirm`), and the association is discarded on
  session end and on reconnect;
* an unknown voice may ask questions but never silently mutates an experiment;
  stop and pause are honoured regardless of attribution; overlapping voices fail
  closed and ask one person to repeat.

### Reviewer and Admin as products, not consoles

An information-architecture and Korean copy pass, not another redesign.

* both pages state their purpose in one sentence above everything else;
* Reviewer gained a "승인하면 어떻게 되나요?" block next to the buttons that
  cause it; actions read 연구자 사용 승인 / 수정 요청 / 사용 중지;
* Admin sections are named after the job they do — 구성원과 역할, 연결 서비스,
  로그인과 데이터 보관, 서비스 상태;
* machine-translated internals were removed from primary copy and are
  regression-tested as absent: 테넌트, 비식별 운영 지표, 접근 제어 활동,
  인증 경계, 원음·대화문·모델 추론.

### Bench microphone status

A readable status line — 마이크 준비됨 / 주변 소음이 큽니다 / 여러 목소리가
겹쳤습니다 / 답변 재생만 중단됨 — whose tone is carried by a glyph and a data
attribute as well as colour. Raw VAD probabilities and noise-floor numbers stay
in the collapsed 상세 정보 panel, where they belong.

---

## 2. Contract-tested only (fakes, no live provider)

| Capability | What was actually exercised |
| --- | --- |
| `diarize` STT request field | The multipart request is built with the field in documented order and only when enabled. No live transcription with diarization has been run. |
| Speaker labels in `words[]` | Parsed from fixture payloads covering both the documented `text` key and this repository's legacy `word` key. |
| Runtime Korean translation | Driven by injected translator callables, including adversarial ones that alter a number, echo English, or invent completion criteria. No live model call. |
| Google OIDC sign-in | Transport and ID-token verifier are injected; the tests drive them with fakes. **No request has ever reached Google**, and no HTTP route is wired up. |

---

## 3. Live-validated in this pass

**Nothing new.** No live xAI call, no real microphone, and no browser audio
capture was exercised while doing this work. The browser acceptance suite runs
against a credential-free server with fake media devices, which validates DOM,
HTTP and WebSocket behaviour — not audio.

Where an earlier pass recorded live evidence, that evidence still stands on its
own terms and is not extended by this pass.

---

## 4. Not yet field-validated

* real wet-lab ambient noise, and the real WebRTC VAD's opinion of it. Every
  fixture in the acceptance sweep is synthetic constant-amplitude PCM with a
  *scripted* VAD verdict — that is exactly the variable a real lab changes;
* real overlapping researchers on a single microphone;
* a real long-duration wet-lab session with gloves, a fume hood and interruptions;
* speaker identification of any kind. Diarization is implemented; **speaker
  verification is not implemented and not validated**;
* production Google login;
* the acoustic assumption behind the playback-onset cooldown, namely that
  browser echo cancellation meaningfully attenuates the speaker-to-microphone
  path on the pilot's actual hardware.

The synthetic sweep reports digital-amplitude ratios. They are not dBA, not
acoustic SNR, and must not be quoted as either.

---

## 5. Validation evidence for this pass

| Gate | Result |
| --- | --- |
| `pytest -q` (workspace and report flags forced off) | **943 passed, 769 subtests passed, 0 failed** (baseline was 844 / 714) |
| `compileall -q src tests scripts` | clean |
| `git diff --check` | clean |
| `scripts/replay_turns.py` | exit 0 |
| `playwright test --config=playwright.ci.config.ts` | **67 passed, 1 skipped, 0 failed** (baseline was 61 / 1) |
| `scripts/evaluate_barge_in.py` | 9/9 scenarios, 0 false candidates, 0 missed interruptions, 0 unintended mutations, exit 0 |

Two pre-existing fixtures were made acoustically consistent rather than relaxed:
barge-in fixtures that paired a scripted "this is speech" VAD verdict with
*digital silence* now carry speech-level amplitude, because digital silence is
precisely what the new gate exists to refuse. Turn-identity tests that drive
onset in three frames with a scripted VAD now pass an explicitly scaled gate
configuration; the gate stays enabled and every assertion is unchanged.

---

## 6. Known limitations

1. **The gate cannot separate loud noise from loud speech by level alone.** It
   relies on the endpoint detector's speech-shape verdict for that, and WebRTC
   VAD mode 3 is imperfect on broadband machine noise. This is the single most
   likely source of a real-lab false barge-in.
2. **Single-microphone overlap is not solved.** With diarization enabled the
   product detects overlap and refuses to act; it does not attribute it.
   Multichannel input is a documented provider capability and is not implemented.
3. **Streaming STT capabilities are unavailable on the path this product uses.**
   `interim_results`, `endpointing`, `smart_turn` and `smart_turn_timeout` are
   documented only for the streaming WebSocket endpoint; this repository calls
   the batch endpoint once per utterance. Adopting them is a pipeline change,
   not a parameter change, and is not implemented or claimed.
4. **Speech-to-Speech was not spiked.** Evaluating xAI's realtime API would need
   live credentials and a benchmark this pass did not run. The deterministic
   Cascade path remains the production pilot path.
5. **Participant rostering is minimal.** The roster is seeded with the
   authenticated principal. A multi-person roster drawn from lab membership, and
   the "이 실험에 참여하는 연구자를 확인해 주세요" setup screen, are not built.
6. **Google login has no HTTP route.** The flow is a library with tests; nothing
   serves it yet.
7. **`ChallengeStore` is in-memory and process-local**, so the Google flow as
   written would not survive two workers. Recorded rather than hidden.
8. **Runtime translation is unproven in practice.** The preservation check
   rejects a translation that alters a measurement; it cannot detect a
   translation that is fluent, measurement-preserving and still subtly wrong.
   That is why it is off by default.

---

## 7. Recommendation for the first real lab pilot

**Run the first trial with the defaults this pass ships, and change one thing at
a time.**

1. **Leave diarization off (`XAI_STT_DIARIZE=0`) and leave runtime translation
   off.** Both are new, both are contract-tested only, and the first trial's job
   is to measure the interruption gate — not to debug three new subsystems at
   once.
2. **Use a headset or a close-talk microphone for the first session.** The gate's
   level criterion assumes the operator is closer to the microphone than the
   room is. Confirm 에코 제거 확인 appears in the developer detail panel before
   starting; if it says 에코 제거 꺼짐, a headset is not optional.
3. **Instrument the session, then read the numbers.** Every rejected candidate
   logs `barge_in.ignored reason=… noise_floor_rms=…`. After one real session,
   compare the observed floor against `CASCADE_BARGE_IN_ONSET_ABSOLUTE_RMS`
   (0.006) and `CASCADE_BARGE_IN_NOISE_FLOOR_MAX_RMS` (0.03). If the measured
   ambient floor sits at or above the maximum, that clamp — not the ratio — is
   what needs raising.
4. **Watch for the opposite failure.** The gate is tuned to prefer "the agent
   kept talking" over "the agent stopped for a chair scrape". If researchers
   report having to repeat themselves to interrupt, lower
   `CASCADE_BARGE_IN_ONSET_SNR_RATIO` from 3.0 toward 2.0 before touching
   anything else.
5. **Run the first session on one protocol whose Korean sidecar is complete**, so
   the Korean-first path is exercised on its reviewed branch. Note every step
   where the researcher sees 원문 그대로: that is the reviewer backlog, and it is
   more valuable than enabling machine translation.
6. **Treat the acceptance sweep as a pre-flight, not as evidence.** Run
   `scripts/evaluate_barge_in.py` before the session to confirm nothing
   regressed, and record the real session separately as the first actual field
   data this product has.
7. **Do not enable Google login for the first trial.** Development-mode sign-in
   is labelled 개발 모드 and is sufficient for a supervised pilot; production
   login should follow real membership requirements, not precede them.

The product should stay labelled **Controlled Pilot Ready — Engineering /
Field-Unvalidated** until at least one full wet-lab session has been run and its
false-barge-in count, overlap count and unintended-mutation count recorded.
