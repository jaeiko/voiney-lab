// The microphone check of the settings and the first guide (lane RX2): the
// microphone opened as a run opens it (voice-core's openMicStream), its level
// read for a few seconds while the sentence is read, and a plain judgement --
// heard well, too quiet, nothing heard. It is this browser's own check: it
// sends nothing and changes no setting (the server's measurement of the
// wearer's voice happens only inside a session; lane RX2 report, item 1).

export interface MicMeter {
  /** The level now, as the root mean square of the last samples (0-1). */
  level(): number;
  label: string;
  stop(): void;
}

/** The same threshold voice-core's capture takes as "a signal" (ensureAudio's micSignalSeen). */
export const VOICED_LEVEL = 0.008;
/** Under this, a voiced reading is too quiet for the server's endpointing to take with ease. */
export const QUIET_LEVEL = 0.02;
export const CHECK_MS = 6000;

export async function openMicMeter(open: () => Promise<MediaStream>): Promise<MicMeter> {
  const stream = await open();
  const context = new AudioContext();
  const source = context.createMediaStreamSource(stream);
  const analyser = context.createAnalyser();
  analyser.fftSize = 1024;
  source.connect(analyser);
  const samples = new Float32Array(analyser.fftSize);
  const track = stream.getAudioTracks?.()[0];
  return {
    label: track?.label ?? "",
    level() {
      analyser.getFloatTimeDomainData(samples);
      let energy = 0;
      for (let i = 0; i < samples.length; i++) energy += samples[i] * samples[i];
      return Math.sqrt(energy / samples.length);
    },
    stop() {
      try {
        source.disconnect();
      } catch {
        /* gone already */
      }
      for (const item of stream.getTracks()) item.stop();
      void context.close().catch(() => undefined);
    },
  };
}

export type VoiceJudgement = "good" | "quiet" | "noSound";

/** What the levels read during the check say: no voiced reading, a quiet one, or a good one (by the voiced readings' median). */
export function judgeVoice(levels: number[]): VoiceJudgement {
  const voiced = levels.filter((value) => value >= VOICED_LEVEL).sort((a, b) => a - b);
  if (voiced.length < 3) return "noSound";
  const median = voiced[Math.floor(voiced.length / 2)];
  return median < QUIET_LEVEL ? "quiet" : "good";
}
