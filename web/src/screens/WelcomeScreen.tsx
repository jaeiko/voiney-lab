import { CircleCheck, Mic } from "lucide-react";
import { useState } from "react";

import type { AuthSession } from "../api/types";
import { Logo } from "../components/Logo";
import { Note } from "../components/Pieces";
import { StepProgress } from "../components/StepProgress";
import { welcome as copy } from "../copy/ko";
import { go, routes } from "../lib/navigation";
import { loadVoiceScripts } from "../voice/engine";
import { micFailureWords, VoiceCheck } from "./settings/VoiceCheck";
import { VoicePicker } from "./settings/VoicePicker";

// The first guide after a login (lane RX2, person's decision 6): once per
// account on this device -- the microphone allowed, the voice checked (may be
// skipped), the guiding voice chosen, then home. "나중에 하기" leaves it too;
// the settings have the same parts again.

const WELCOME_KEY = "voiney-lab.app-welcome";

export function welcomeKey(accountId: string): string {
  return `${WELCOME_KEY}:${accountId}`;
}

/** Whether this account still has its first guide to see on this device (a browser without storage: no guide, never a loop). */
export function welcomeDue(session: AuthSession): boolean {
  const id = session.mode === "accounts" && session.signed_in ? session.account?.account_id : null;
  if (!id) return false;
  try {
    return globalThis.localStorage?.getItem(welcomeKey(id)) !== "done";
  } catch {
    return false;
  }
}

export function markWelcomeDone(session: AuthSession): void {
  const id = session.account?.account_id;
  if (!id) return;
  try {
    globalThis.localStorage?.setItem(welcomeKey(id), "done");
  } catch {
    /* a browser that keeps nothing shows no guide */
  }
}

async function allowMicrophone(): Promise<string> {
  const media = globalThis.navigator?.mediaDevices;
  if (typeof media?.getUserMedia !== "function") throw new Error(copy.micNoHttps);
  const stream = await media.getUserMedia({ audio: true });
  const label = stream.getAudioTracks?.()[0]?.label || "";
  for (const track of stream.getTracks()) track.stop();
  return label;
}

export function WelcomeScreen({ session, allow = allowMicrophone }: { session: AuthSession; allow?: () => Promise<string> }) {
  const [index, setIndex] = useState(0);
  const [mic, setMic] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  function finish() {
    markWelcomeDone(session);
    go(routes.home, true);
  }

  async function askMicrophone() {
    setNote(null);
    try {
      const label = await allow();
      setMic(label || copy.micDefault);
    } catch (error) {
      await loadVoiceScripts().catch(() => undefined);
      setNote(micFailureWords(error, copy.micBlocked, copy.micBlocked));
    }
  }

  return (
    <div className="welcome" data-testid="welcome-screen">
      <div className="welcome-card">
        <Logo height={40} />
        <h1 className="screen-title">{copy.title}</h1>
        <p className="muted">{copy.lead}</p>
        <StepProgress names={copy.stepNames} index={index} label={copy.title} />
        <p className="start-why" data-testid="welcome-why">{copy.why[index]}</p>
        {index === 0 ? (
          <div className="welcome-step" data-step="mic">
            {mic ? (
              <p className="saved" role="status" data-testid="welcome-mic-ok">
                <CircleCheck aria-hidden="true" />
                <span>{copy.micAllowed(mic)}</span>
              </p>
            ) : (
              <button type="button" className="btn btn-primary btn-big" onClick={() => void askMicrophone()} data-testid="welcome-mic">
                <Mic aria-hidden="true" />
                <span>{copy.micAllow}</span>
              </button>
            )}
            {note ? <Note>{note}</Note> : null}
          </div>
        ) : index === 1 ? (
          <div className="welcome-step" data-step="voice">
            <VoiceCheck skip={{ label: copy.skip, onSkip: () => setIndex(2) }} />
          </div>
        ) : (
          <div className="welcome-step" data-step="agent">
            <VoicePicker />
          </div>
        )}
        <div className="start-footer">
          <button type="button" className="btn btn-quiet" onClick={finish} data-testid="welcome-later">
            {copy.later}
          </button>
          <div className="start-footer-primary">
            {index < 2 ? (
              <button type="button" className="btn btn-primary btn-big" disabled={index === 0 && !mic} onClick={() => setIndex(index + 1)} data-testid="welcome-next">
                {copy.next}
              </button>
            ) : (
              <button type="button" className="btn btn-primary btn-big" onClick={finish} data-testid="welcome-done">
                {copy.done}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
