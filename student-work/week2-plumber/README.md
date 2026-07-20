# M2 Plumber

A minimal push-to-talk voice assistant that carries raw 16 kHz mono PCM16 over
a browser WebSocket, frames it into exact 20 ms units on the server, runs an
end-of-utterance xAI STT → Grok → xAI TTS cascade, and plays framed PCM replies.

## Setup

Create and activate a virtual environment, then install the declared packages:

```bash
cd student-work/week2-plumber
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Provide configuration in your shell environment or in a local `.env` (which is
git-ignored). Never commit a key:

```bash
export XAI_API_KEY="paste-your-key-locally"
export CHAT_MODEL="choose-a-current-chat-model-from-docs.x.ai"
export TTS_VOICE="eve"
```

Optional: `XAI_BASE_URL` defaults to `https://api.x.ai/v1`.

## Architecture

The browser requests a mono microphone, converts Web Audio float samples to
signed little-endian PCM16, and sends binary WebSocket messages while the PTT
button is held. The server buffers arbitrary WebSocket chunk sizes into 640-byte
frames: 320 samples × 2 bytes = exactly 20 ms at 16 kHz. On release it zero-pads
only the final partial frame.

The selected input path is either:

- **clean:** validated 16 kHz PCM16 unchanged;
- **phone:** linear downsample to 8 kHz, explicit ITU-T G.711 μ-law encode and
  decode, then linear upsample to 16 kHz;
- **compare:** derives both paths from the same capture, sends both previews to
  the browser, transcribes both paths with separate STT timings, displays both
  transcripts side by side, then uses only the clean transcript for one Grok
  and TTS cascade.

The server wraps selected PCM in an in-memory WAV for end-of-utterance STT,
sends the transcript to Grok chat completions, and asks xAI TTS for raw 16 kHz
PCM. TTS validation checks the HTTP status, rejects JSON/error bodies, requires
non-empty even-length PCM, and deliberately does not require one exact success
Content-Type. Replies are again padded/framed into exact 20 ms binary messages.
The browser schedules playback with a basic 60 ms (three-frame) lead buffer.

The μ-law codec is pure Python and tested against known G.711 values and all
256 encoded bytes. It does not use `audioop`, whose availability changed across
Python versions.

## WebSocket message protocol

Client text controls:

```json
{"type":"capture.start","mode":"clean"}
{"type":"capture.stop"}
```

Between those controls, client binary messages are raw 16 kHz mono PCM16LE.
They may be any even size; the server owns exact framing and partial buffering.

Server text events include `ready`, `capture.started`, `turn.processing`,
`transcript`, `comparison.result`, `reply`, `audio.start`, `audio.end`,
`turn.done`, and `error`. In compare mode, `comparison.result` contains both
transcripts and separate clean/phone STT timings.
Every run of server binary messages is bracketed by `audio.start`/`audio.end`.
`audio.start` names the stream (`tts`, `clean-preview`, or `phone-preview`) and
declares `pcm_s16le`, 16 kHz, mono, 20 ms, and the exact frame count. Every
server binary message is one 640-byte frame. `turn.done` carries stage latency,
mode, input frame count, and output frame count.

## Run

```bash
uvicorn server:app --reload --port 8000
```

Open `http://localhost:8000`. Microphone capture requires localhost or HTTPS.
Hold the button, speak, and release. Compare mode exposes two preview buttons
made from one capture, displays both STT results side by side, and uses the
clean result for a single assistant response.

## Offline tests

These commands do not contact xAI:

```bash
python -m unittest discover -s tests -v
python -m py_compile audio.py protocol.py server.py
```

Tests cover PCM conversion and validation, exact 20 ms partial buffering and
padding, linear resampling, μ-law known values/full byte domain/quality bounds,
clean and phone paths, WAV metadata, JSON controls/events, outbound metadata,
TTS response validation, and outbound exact framing.

## Known limitations
The compare orchestration test mocks every external stage and verifies two STT
calls but only one Grok and one TTS call, so it never contacts xAI.

- Processing starts only on PTT release; STT, chat, and TTS are not streamed.
- One WebSocket handles one turn at a time and has no cancellation or history.
- The browser uses the compact, deprecated `ScriptProcessorNode`; a production
  client should use an `AudioWorklet` and a streaming sample-rate converter.
- Browsers that refuse the requested 16 kHz `AudioContext` are rejected rather
  than silently sending mislabeled PCM.
- Linear resampling is intentionally educational, not a production anti-aliasing
  filter. The comparison previews remain in memory for the page lifetime.
- There is no VAD, barge-in, authentication, rate limiting, jitter injection,
  advanced queue telemetry, or exhaustive keyboard/touch behavior.
- Actual microphones, browser audio scheduling, current paid xAI models/voice,
  and live API response behavior require manual verification.

