// 목소리 맞추기 on the new screen (lane RX2 over lane HEAR's contract,
// docs/FEATURES.md F8): the sentence is recorded on the microphone runs use,
// as runs use it (voice-core's recordCalibrationPcm: the chosen device, the
// gain control off in "목소리를 맞추면 끔", 16 kHz PCM16), and posted to
// POST /api/voice/calibration with the microphone's short id. The server
// measures the voiced part, keeps only the level, when and which microphone
// (the experimenter setting voice_reference), and drops the sound.
import { ApiError, postJson } from "../api/client";
import { encodePcm, recordVoice } from "./engine";

/** As long as index.html's VOICE_MEASURE_SECONDS (tests/test_lane_rx2_voice_core.py compares them). */
export const MEASURE_SECONDS = 5;
/** VOICE_REFERENCE_MAX_AGE_DAYS on the server: an older measurement is not used. */
export const MAX_AGE_DAYS = 14;

export interface Measured {
  state: "measured";
  level_db: number;
  measured_at: string;
  mic_id: string;
  speaker_filter: string;
  max_age_days: number;
}

export type MeasureFailure = "too_little_voice" | "failed";

export interface Recorder {
  record: (seconds: number) => Promise<{ pcm: Int16Array; micId: string | null }>;
  encode: (pcm: Int16Array) => string;
}

export const browserRecorder: Recorder = { record: recordVoice, encode: encodePcm };

/** Record the sentence and have the server measure it; a refusal is its code. */
export async function measureVoice(recorder: Recorder = browserRecorder): Promise<Measured> {
  const recorded = await recorder.record(MEASURE_SECONDS);
  try {
    return await postJson<Measured>("/api/voice/calibration", { pcm_base64: recorder.encode(recorded.pcm), mic_id: recorded.micId });
  } catch (error) {
    throw new MeasureError(error instanceof ApiError && error.code === "too_little_voice" ? "too_little_voice" : "failed");
  }
}

export class MeasureError extends Error {
  readonly reason: MeasureFailure;
  constructor(reason: MeasureFailure) {
    super(reason);
    this.reason = reason;
  }
}

export interface VoiceReference {
  levelDb: number;
  measuredAt: number;
  micId: string;
}

/** The setting's value "v1:<dBFS>:<unix seconds>:<microphone id>" (server.py parse_voice_reference), or null. */
export function parseVoiceReference(value: unknown): VoiceReference | null {
  const match = typeof value === "string" ? /^v1:(-?\d{1,3}(?:\.\d)?):(\d{9,11}):([A-Za-z0-9][A-Za-z0-9_-]{0,39})$/.exec(value) : null;
  if (!match) return null;
  const level = Number(match[1]);
  if (!(level >= -100 && level <= 0)) return null;
  return { levelDb: level, measuredAt: Number(match[2]) * 1000, micId: match[3] };
}

/** Whether the measurement is too old to be used (the server's 14 days). */
export function referenceStale(reference: VoiceReference, now: number = Date.now()): boolean {
  return now - reference.measuredAt > MAX_AGE_DAYS * 86400 * 1000;
}
