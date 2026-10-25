import { Mic } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Note } from "../../components/Pieces";
import { settings as copy } from "../../copy/ko";
import { openCheckMicrophone } from "../../voice/engine";
import { CHECK_MS, judgeVoice, openMicMeter, type MicMeter, type VoiceJudgement } from "../../voice/micCheck";

// "목소리 맞추기" (person's decisions 5 and 6): only in the settings and the
// first guide, never during a run, and it may be skipped. The sentence is
// read while the level shows; the result says whether the voice is heard
// well. It is this browser's check (micCheck.ts).

type Phase = "idle" | "opening" | "listening" | "done";

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

const METER_FULL = 0.1;

export function VoiceCheck({
  open = openCheckMicrophone,
  onDone,
  skip,
}: {
  open?: () => Promise<MediaStream>;
  onDone?: (result: VoiceJudgement) => void;
  skip?: { label: string; onSkip: () => void };
}) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [level, setLevel] = useState(0);
  const [left, setLeft] = useState(CHECK_MS);
  const [result, setResult] = useState<VoiceJudgement | null>(null);
  const [error, setError] = useState<string | null>(null);
  const meter = useRef<MicMeter | null>(null);
  const timers = useRef<{ tick?: ReturnType<typeof setInterval>; end?: ReturnType<typeof setTimeout> }>({});

  function stop() {
    clearInterval(timers.current.tick);
    clearTimeout(timers.current.end);
    meter.current?.stop();
    meter.current = null;
  }
  useEffect(() => stop, []);

  async function begin() {
    stop();
    setError(null);
    setResult(null);
    setPhase("opening");
    try {
      meter.current = await openMicMeter(open);
    } catch (failure) {
      setPhase("idle");
      setError(micFailureWords(failure, copy.voiceCheck.blocked, copy.voiceCheck.failed));
      return;
    }
    const levels: number[] = [];
    const started = Date.now();
    setLeft(CHECK_MS);
    setPhase("listening");
    timers.current.tick = setInterval(() => {
      const value = meter.current?.level() ?? 0;
      levels.push(value);
      setLevel(value);
      setLeft(Math.max(0, CHECK_MS - (Date.now() - started)));
    }, 100);
    timers.current.end = setTimeout(() => {
      stop();
      const judged = judgeVoice(levels);
      setResult(judged);
      setPhase("done");
      onDone?.(judged);
    }, CHECK_MS);
  }

  function cancel() {
    stop();
    setPhase("idle");
  }

  const percent = Math.round(Math.min(1, level / METER_FULL) * 100);
  return (
    <div className="voice-check" data-phase={phase} data-testid="voice-check">
      {phase === "listening" || phase === "opening" ? (
        <>
          <p>{copy.voiceCheck.sentence}</p>
          <blockquote className="voice-check-sentence">{copy.voiceCheck.readSentence}</blockquote>
          <div className="voice-check-meter" role="meter" aria-label={copy.voiceCheck.level} aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}>
            <span style={{ width: `${percent}%` }} />
          </div>
          <p className="muted" role="status">
            {copy.voiceCheck.listening} {Math.ceil(left / 1000)}
          </p>
          <div className="voice-check-actions">
            <button type="button" className="btn btn-quiet" onClick={cancel}>
              {copy.voiceCheck.stop}
            </button>
          </div>
        </>
      ) : (
        <>
          {result ? (
            <p className="voice-check-result" data-result={result} role="status" data-testid="voice-check-result">
              {result === "good" ? copy.voiceCheck.good : result === "quiet" ? copy.voiceCheck.quiet : copy.voiceCheck.noSound}
            </p>
          ) : null}
          {error ? <Note>{error}</Note> : null}
          <div className="voice-check-actions">
            <button type="button" className={`btn ${result ? "" : "btn-primary"}`} onClick={() => void begin()} data-testid="voice-check-start">
              <Mic aria-hidden="true" />
              <span>{result ? copy.voiceCheck.again : copy.voiceCheck.start}</span>
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
