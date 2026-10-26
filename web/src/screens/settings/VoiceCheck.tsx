import { Mic } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Note } from "../../components/Pieces";
import { settings as copy } from "../../copy/ko";
import { browserRecorder, MEASURE_SECONDS, MeasureError, type MeasureFailure, measureVoice, type Measured, type Recorder } from "../../voice/micCheck";

// "목소리 맞추기" (person's decisions 5 and 6): only in the settings and the
// first guide, never during a run, and it may be skipped. The sentence is
// recorded on the run's microphone and the server keeps its level (lane HEAR,
// POST /api/voice/calibration); the side-voice filter can be turned on after.

type Phase = "idle" | "recording" | "sending" | "done";

/** The plain words for a measurement that failed (lane HEAR2, decision 6): what happened, then what to do. */
export function measureFailureWords(reason: MeasureFailure): string {
  switch (reason) {
    case "too_little_voice": return copy.voiceCheck.tooLittle;
    case "audio_length": return copy.voiceCheck.tooShort;
    case "audio_invalid": return copy.voiceCheck.badAudio;
    case "server": return copy.voiceCheck.serverDown;
    case "network": return copy.voiceCheck.noConnection;
    default: return copy.voiceCheck.notMeasured;
  }
}

/** The words for a microphone that would not open: the device's own place to allow it (voice-core's micBlockedWords), the page's address, or a plain sentence. */
export function micFailureWords(error: unknown, blocked: string, failed: string): string {
  const name = (error as { name?: string } | null)?.name ?? "";
  if (name === "NotAllowedError" || name === "SecurityError") {
    const device = (globalThis as { micBlockedWords?: (error: unknown) => string | null }).micBlockedWords?.(error);
    return device || blocked;
  }
  const message = (error as { message?: unknown } | null)?.message;
  if (typeof message === "string" && message.includes("HTTPS")) return message;
  return failed;
}

export function VoiceCheck({
  recorder = browserRecorder,
  onMeasured,
  skip,
}: {
  recorder?: Recorder;
  onMeasured?: (result: Measured) => void;
  skip?: { label: string; onSkip: () => void };
}) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [left, setLeft] = useState(MEASURE_SECONDS);
  const [measured, setMeasured] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const tick = useRef<ReturnType<typeof setInterval> | undefined>(undefined);
  useEffect(() => () => clearInterval(tick.current), []);

  async function begin() {
    setError(null);
    setMeasured(false);
    setPhase("recording");
    setLeft(MEASURE_SECONDS);
    const started = Date.now();
    clearInterval(tick.current);
    tick.current = setInterval(() => {
      const remaining = Math.max(0, MEASURE_SECONDS - Math.floor((Date.now() - started) / 1000));
      setLeft(remaining);
      if (remaining === 0) setPhase((now) => (now === "recording" ? "sending" : now));
    }, 250);
    try {
      const result = await measureVoice(recorder);
      setMeasured(true);
      setPhase("done");
      onMeasured?.(result);
    } catch (failure) {
      setPhase("idle");
      if (failure instanceof MeasureError) setError(measureFailureWords(failure.reason));
      else setError(micFailureWords(failure, copy.voiceCheck.blocked, copy.voiceCheck.failed));
    } finally {
      clearInterval(tick.current);
    }
  }

  return (
    <div className="voice-check" data-phase={phase} data-testid="voice-check">
      {phase === "recording" || phase === "sending" ? (
        <>
          <p>{copy.voiceCheck.sentence}</p>
          <blockquote className="voice-check-sentence">{copy.voiceCheck.readSentence}</blockquote>
          <p className="muted" role="status">
            {phase === "sending" ? copy.voiceCheck.measuring : `${copy.voiceCheck.listening} ${left}`}
          </p>
        </>
      ) : (
        <>
          {measured ? (
            <p className="voice-check-result" data-result="measured" role="status" data-testid="voice-check-result">
              {copy.voiceCheck.measured}
            </p>
          ) : null}
          {error ? <Note>{error}</Note> : null}
          <div className="voice-check-actions">
            <button type="button" className={`btn ${measured ? "" : "btn-primary"}`} onClick={() => void begin()} data-testid="voice-check-start">
              <Mic aria-hidden="true" />
              <span>{measured ? copy.voiceCheck.again : copy.voiceCheck.start}</span>
            </button>
            {skip ? (
              <button type="button" className="btn btn-quiet" onClick={skip.onSkip} data-testid="voice-check-skip">
                {skip.label}
              </button>
            ) : null}
          </div>
        </>
      )}
    </div>
  );
}
