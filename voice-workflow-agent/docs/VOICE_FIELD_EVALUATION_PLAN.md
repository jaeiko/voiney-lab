# Voice Field Evaluation Plan

This plan measures bench-side voice reliability; it does not claim field performance that has not been observed. The checked-in harness consumes transcription/routing/timing result manifests and emits aggregates only. It never opens or persists audio.

## Metrics and release gates

Measure word error rate, semantic-intent accuracy, workflow-command accuracy, false-mutation rate, VAD start/end error, endpoint latency, barge-in latency, and correction/repeat rate. Report overall values and stratify them by the matrix below. A pilot release must set numeric gates before data collection; in particular, the false-mutation gate for language-mismatched completion, stop, resume, observation, confirmation, and protocol-selection utterances should be zero.

| Dimension | Initial levels |
| --- | --- |
| Acoustic environment | Clean room, fan, centrifuge-like noise, background conversation |
| Signal-to-noise ratio | Clean/no added noise, 20 dB, 10 dB, 5 dB |
| Face covering | None, surgical mask, respirator |
| Microphone distance | 30 cm, 60 cm, 120 cm |
| Language | Korean and English session preferences |
| Korean variation | Multiple regions/accents and slow, normal, fast speech |
| Interaction | Ordinary turn, interruption/barge-in, correction/repeat |

Each command class needs positive cases, near-neighbor cases, and adversarial background-speech cases. Korean sessions must include cases where an STT provider returns English or mostly Latin-script text for Korean speech; those cases pass only when routing records a language mismatch and authorizes no mutation.

## Offline synthetic/noise-mixed evaluation

1. Generate or license clean, non-private command fixtures. Give each an opaque fixture ID.
2. Mix reproducible public or synthetic noise locally at the declared SNR. Do not add the resulting audio to Git unless its license and repository policy explicitly permit that.
3. Exercise the production microphone/STT/routing boundary, not a helper-only classifier.
4. Record the expected and actual transcripts, intents, mutation booleans, VAD boundaries, and timings in a local result manifest.
5. Run `voice-workflow-evaluate path/to/results.json` (or `python -m voice_workflow_agent.voice_evaluation path/to/results.json`). Archive the aggregate JSON with the tested build SHA and provider/model identifiers.

The result manifest format is defined by `EvaluationCase` in `voice_evaluation.py`. It contains no audio path or audio bytes. Synthetic evaluation is useful for repeatability but must not be presented as real bench performance.

## Consented field study

Field recordings are opt-in. Before collection, document participant consent, purpose, access list, encryption, storage region, deletion process, and a retention period of at most 365 days. Use a study-specific store outside application runtime data. Never enable the existing STT diagnostic capture as a substitute for informed-consent collection.

The harness rejects a `consented_field_recording` case unless it has an opaque consent ID and bounded retention days. The aggregate output deliberately omits consent IDs, transcripts, recordings, secrets, and model reasoning. Delete source recordings on schedule and retain only approved aggregates.

## Pilot execution and triage

Run at least two devices representative of the tablet/microphone deployment. Randomize condition order, include repeated speakers, and log network/provider incidents separately from acoustic errors. For every false mutation, preserve only the minimum consented evidence needed for diagnosis, reproduce it with a synthetic fixture where possible, fix the production WebSocket path, and rerun the complete matrix. Report unavailable cells as “not tested,” never as zero errors.
