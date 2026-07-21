# Week 4 M4 Brain — SafeBridge

M4 adds SafeBridge persona, local search_approved_safety_manual function calling, bounded in-memory history, streamed Grok sentence chunking, sequential sentence TTS, ordered segmented playback, and end-of-speech to first-audio metrics. Week 3 behavior remains intact.

M3 removes per-turn push-to-talk. One Start Session gesture unlocks the browser
microphone and speaker; after that, WebRTC VAD and endpointing automatically run
each complete utterance through xAI STT → Grok → xAI TTS. The definition of done
is a hands-free multi-turn conversation in which a short mid-sentence pause does
not split the turn and background noise does not call the cascade.

## Architecture and state

The browser continuously sends mono PCM16LE while a session is active. The
server converts arbitrary WebSocket chunks to exact frames, then a bounded
endpoint detector keeps prefix audio in IDLE and utterance audio only in
USER_SPEAKING:

    browser mic → FrameBuffer → WebRTC VAD → endpoint detector
        → STT → Grok → TTS → framed browser playback

The server owns this lifecycle:

    IDLE → USER_SPEAKING → PROCESSING → AGENT_SPEAKING
         → COOLDOWN → IDLE

Endpoint commit atomically enters PROCESSING and detaches the utterance. Frames
received during PROCESSING, AGENT_SPEAKING, and cooldown are discarded. The
server enters AGENT_SPEAKING before TTS delivery and does not leave it on
`audio.end`; it waits for the matching browser `playback.ended`.

## Audio contract and tuning

Inbound and outbound audio is 16,000 Hz, mono, signed little-endian PCM16.
Each VAD/playback frame is exactly 20 ms = 320 samples = 640 bytes.

Settings tuned after the first hands-free live test:

| Setting | Value | Reason |
|---|---:|---|
| WebRTC VAD mode | 3 | More aggressively rejects ambient noise after live-test false activations |
| Speech onset | 4 voiced of the last 6 frames | Requires more consistent speech evidence before opening a turn |
| Prefix padding | 15 frames / 300 ms | Preserve initial consonants and onset frames |
| Endpoint silence | 50 frames / 1000 ms | Preserve sentence-level pauses with a configurable grace period |
| Minimum speech | 12 voiced frames / 240 ms | Reject short noise bursts before STT |
| Maximum utterance | 750 frames / 15 s | Bound memory and stuck-positive VAD |
| Post-playback cooldown | 300 ms | Reject speaker/room echo tails |

Only the final consecutive endpoint silence is trimmed. Prefix and short
internal silence remain in frame order.

Mode 3 and 4-of-6 onset were selected because the first live test preserved real
speech and mid-sentence pauses but ordinary ambient noise still opened turns.
This tuning favors fewer false activations at the cost of possibly missing very
quiet speech. Short confirmations may need retuning after more live testing.
The browser explicitly disables automatic gain control while keeping echo
cancellation and noise suppression enabled: automatic gain can amplify quiet
background noise and make VAD behavior less predictable across rooms and pauses.

## WebSocket protocol

Client text controls:

```json
{"type":"session.start"}
{"type":"session.stop"}
{"type":"playback.ended","turn_id":1}
```

Binary client messages are raw PCM and may be any size; the server owns exact
framing. Turn-specific server events carry `turn_id`: `speech.start`,
`speech.end`, `speech.rejected`, `turn.processing`, `transcript`,
`reply`, `audio.start`, `audio.end`, `turn.done`, and turn errors.
Stale playback acknowledgements are ignored.

## Setup and run

```bash
cd student-work/week4-brain
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
uvicorn server:app --reload --port 8000
```

Open `http://localhost:8000`, click Start Session once, and then speak without
using a per-turn control. Localhost or HTTPS is required for microphone access.

`webrtcvad-wheels==2.0.14` is used because the original native
`webrtcvad` distribution can be difficult to build on Python 3.12. The
installed import name remains `webrtcvad`. Do not install both distributions.

## Offline verification

These tests use synthetic PCM, injected VAD decisions, and mocked STT/Grok/TTS;
they make no API calls:

```bash
python -m pip check
python -c "import webrtcvad; v=webrtcvad.Vad(3); print(v.is_speech(bytes(640), 16000))"
python -m unittest discover -s tests -v
python -m py_compile audio.py protocol.py server.py vad.py \
  tests/test_audio.py tests/test_protocol.py tests/test_server_helpers.py tests/test_vad.py
```

Coverage includes silence, 4-of-6 onset (and 3-of-6 non-onset), ordered prefix without duplication,
39-frame internal pauses, 40-frame endpoints and trimming, noise rejection,
maximum duration, exactly one mocked cascade, ignored audio in gated states,
playback matching, deterministic cooldown, cleanup, and exact framing.
Successful empty STT transcripts are rejected as `empty_transcript` without
calling Grok or TTS; HTTP, parsing, and other cascade failures remain errors.

## Manual three-turn test

1. Start one session and ask a short factual question.
2. After playback and cooldown, say a sentence with a 400–600 ms mid-sentence
   pause; confirm it produces one turn.
3. After the second reply, ask a third question without clicking again.
4. Confirm TTS does not trigger a user turn, a click/keyboard noise does not
   call Grok, logs show one start/end pair per accepted turn, and Stop Session
   clears the state.

## Known limitations

- API stages run only after endpointing and are not streamed.
- There is no barge-in; microphone audio during processing/playback is dropped.
- There is no semantic endpointing, Silero, native speech-to-speech, authentication, or rate limiting. M4 history is in-memory only and the single tool uses demo records.
- The compact teaching UI uses deprecated `ScriptProcessorNode`; a production
  client should use `AudioWorklet` and robust sample-rate conversion.
- Browsers that refuse a 16 kHz `AudioContext` are rejected rather than
  sending mislabeled PCM.
- Real microphones, browser scheduling, noisy rooms, and paid API behavior need
  manual verification.
