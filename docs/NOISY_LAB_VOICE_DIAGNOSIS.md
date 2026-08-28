# Phase 0 diagnosis — audio path, barge-in, and outcome coupling

Read-only audit performed before any change in this pass. Every statement below
was verified against code in this working tree, not assumed from the previous
handoff documents.

## 1. The audio path as it actually exists

```
browser getUserMedia(channelCount:1, echoCancellation/noiseSuppression/autoGainControl)
  -> AudioWorklet "voice-workflow-mic-capture"  (20 ms Float32 batches)
  -> resamplePcm16(...)                          (browser rate -> 16 kHz PCM16)
  -> WebSocket binary frames                     (no client-side gating)
  -> ListenerSession.accept_chunk
  -> FrameBuffer                                 (exact 20 ms / 640-byte frames)
  -> EndpointDetector + webrtcvad (mode 3)
  -> xAI batch STT (multipart REST, one request per committed utterance)
  -> language.classify_input_event admission
  -> RequestArbitration / curated protocol state machine
  -> server-owned workflow mutation
  -> xAI TTS -> WebSocket audio segments -> browser AudioBufferSourceNode
```

Confirmed details:

* `static/index.html` requests `echoCancellation`, `noiseSuppression` and
  `autoGainControl`, but only after checking
  `navigator.mediaDevices.getSupportedConstraints()`, and it reports the
  **actual** track settings back to the server (`client.audio_constraints`).
  This part was already correct.
* The browser computes a frame RMS, but **only** to flip a one-shot
  "mic signal seen" status string (`>= 0.008`). Every captured frame is sent to
  the server regardless of level. There is no client-side gate.
* STT is **batch REST per utterance**, not a persistent stream. There is no
  long-lived STT socket to keep open or reconnect across turns.
* `Transcription.words` is parsed and retained, and already keeps a `speaker`
  key when the provider supplies one — but nothing consumes it. It only feeds
  `word_count` and the opt-in STT diagnostic dump.

## 2. What triggers barge-in today

Barge-in is already two-stage, which is better than the goal assumed, but the
first stage is decided by frame voting alone:

| Stage | Trigger | Effect |
| --- | --- | --- |
| candidate | `EndpointDetector` (playback profile: 12 voiced frames in a 15-frame window) reports `speech_started` while state is `PROCESSING`/`AGENT_SPEAKING` | server emits `barge_in_candidate`; browser **ducks** the cascade gain to `0.04`. Playback is *not* cancelled. |
| audio ready | the interrupt detector endpoints a bounded utterance | server emits `barge_in_audio_ready` and runs STT on the captured audio |
| confirmed | STT returns text **and** `classify_input_event` admits it | `commit_interrupt_candidate` → `assistant.interrupted` + `cascade.playback.clear(reason="confirmed_speech")` → browser hard-stops playback |
| rejected | STT fails/empty, admission rejects, or the candidate is stale | `barge_in_rejected` → browser restores gain and playback continues |

So TTS is **never** hard-cancelled by a single VAD trigger. The real defects are
narrower and specific:

* **D1 — no acoustic evidence beyond webrtcvad.** Mode 3 fires on clattering
  glassware, chair scrapes, fume-hood transients and distant speech. Onset is a
  pure voiced-frame vote with no energy, no noise floor, and no SNR margin.
  Every such event ducks the agent to 4 % volume for up to
  `endpoint_silence_ms` (1000 ms) + one STT round trip, which a bench user
  experiences as "it stopped talking to me".
* **D2 — no playback-onset cooldown.** The interrupt detector is armed at the
  exact instant TTS starts, so the first speaker-to-microphone echo burst is a
  candidate on any machine where browser echo cancellation is weak or absent.
* **D3 — no fast path for a stop command.** "멈춰" must wait for a full 1000 ms
  endpoint silence plus an STT round trip before playback stops.
* **D4 — `playback_ended` can be refused indefinitely.** It returns `False`
  while `_interrupt_candidate_identity is not None`. A noise candidate that
  never endpoints (sustained equipment hum keeps the detector in
  `USER_SPEAKING`) leaves the session pinned in `AGENT_SPEAKING`, and the
  browser's `playback.ended` control is dropped.
* **D5 — no notion of who spoke.** Any nearby voice that reaches the microphone
  is treated as the active experiment operator.

## 3. Playback outcome vs. workflow outcome — are they conflated?

**In the server: no. In the UI: yes.**

The mutation boundary is genuinely separate. `commit_interrupt_candidate` only
bumps `generation`, allocates a superseding turn, and resets detectors; it never
touches `CuratedProtocolSession`, the experiment report store, or any workflow
state. A workflow mutation becomes durable inside the curated-protocol/report
path well before TTS is synthesised, so an interruption during the spoken
acknowledgment cannot roll it back.

But the researcher UI cannot express that. `turnNode(...)` renders a single
`.turn-status` from the canonical turn-state enum, and a superseded turn is
labelled **중단됨** ("cancelled") with no surviving statement of what the turn
actually did. The three-line card (`HEARD · 들은 말` / `UNDERSTOOD · 이해` /
`SAVED · 저장`) also forces a "SAVED" row onto every turn, including pure
questions that correctly changed nothing.

So the fix is presentational and event-shaped, not a workflow-engine change:
the server must publish the semantic outcome, the persistence outcome and the
playback outcome as three separate facts, and the card must render them as
three separate facts.

## 4. Where thresholds live

* `configuration.py::CascadeVadSettings` — all frame-count thresholds, every one
  environment-overridable and range-validated. Playback vs. listening onset
  windows already differ (12/15 vs 8/12).
* `configuration.py::CascadeSttSettings` — `vad_threshold` (0.0–1.0) and
  `filler_words` only.
* `vad.py::VadConfig` — the frame-domain mirror of the above.

There is no adaptive component anywhere: every threshold is a fixed constant
chosen at process start.

## 5. xAI STT capabilities the repo does not use

Checked against the current published contract for the batch multipart endpoint
this repository actually calls:

| Field | Documented | Used here before this pass |
| --- | --- | --- |
| `format`, `language`, `keyterm`, `filler_words`, `vad_threshold` | yes | **yes** |
| `diarize` | yes | no |
| `multichannel`, `channels` | yes | no |
| `audio_format`, `sample_rate`, `url` | yes | n/a (WAV file upload) |

Streaming-only capabilities — `interim_results`, `endpointing`, `smart_turn`,
`smart_turn_timeout` — are **not** available on the batch endpoint this
repository uses. Adopting them means adopting a persistent STT WebSocket, which
is a pipeline change, not a parameter change. That is recorded as future work,
not implemented, and not claimed.

Response `words[]` entries carry `speaker` when `diarize=true`, which is the
seam this pass consumes.

## 6. Conclusion — what this pass should change

Not the voice stack. Four surgical changes:

1. A deterministic **interruption gate** in front of the existing candidate
   stage: adaptive noise floor, SNR margin, minimum sustained voiced duration,
   playback-onset cooldown, and a priority stop-command fast path. It decides
   *candidate vs. ignore*; STT admission still decides *confirmed*.
2. A **turn outcome triple** (semantic / persistence / playback) published by
   the server and rendered as three separate Korean facts.
3. A **presentation/translation boundary** so a Korean turn answers in Korean
   with the approved source kept verbatim behind 원문 보기.
4. **Provider-neutral diarization + participant policy**, off by default, so an
   unknown nearby voice cannot silently mutate an experiment.
