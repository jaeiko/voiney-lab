// The experiment screen's one store (lane RX2): the voice state voice-core.js
// reports through renderState, whether a session runs, and the RunModel the
// server's events build. React reads it with useRun(); the voice engine
// writes it. Nothing here is sent back to the server.
import { useSyncExternalStore } from "react";

import { applyServerEvent, EMPTY_RUN, type EventContext, type RunModel, type VoiceState } from "./runModel";

export interface RunSnapshot {
  voice: VoiceState;
  /** Words the engine gave with an ERROR (a failure, a refusal), shown as they are. */
  detail: string;
  /** A session is accepted and the run's audio is on (voice-core's sessionActive). */
  active: boolean;
  /** A start was sent and its answer is awaited, or a stop is under way. */
  phase: "idle" | "starting" | "running" | "stopping";
  /** The browser holds the sound back until a touch (lane TAB). */
  audioHeld: boolean;
  /** The microphone is open. */
  micOn: boolean;
  /** The microphone was lost and not opened again: voice-core's words for it. */
  micProblem: string | null;
  /** The saved experiment the server bound this run to. */
  experimentId: string | null;
  /** The protocol the last start named (a refused start goes back to its start flow). */
  startedProtocolId: string | null;
  /** The server refused the start: its reason code and its own words. */
  startRefusal: { reason: string; words: string } | null;
  model: RunModel;
}

const INITIAL: RunSnapshot = {
  voice: "IDLE",
  detail: "",
  active: false,
  phase: "idle",
  audioHeld: false,
  micOn: false,
  micProblem: null,
  experimentId: null,
  startedProtocolId: null,
  startRefusal: null,
  model: EMPTY_RUN,
};

let snapshot: RunSnapshot = INITIAL;
const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of [...listeners]) listener();
}

export const runStore = {
  get(): RunSnapshot {
    return snapshot;
  },
  set(change: Partial<RunSnapshot>): void {
    const next = { ...snapshot, ...change };
    if (Object.keys(change).every((key) => (snapshot as unknown as Record<string, unknown>)[key] === (next as unknown as Record<string, unknown>)[key])) return;
    snapshot = next;
    emit();
  },
  event(message: Record<string, unknown>, context: EventContext): void {
    const model = applyServerEvent(snapshot.model, message, context);
    if (model !== snapshot.model) {
      snapshot = { ...snapshot, model };
      emit();
    }
  },
  /** A new run: the screen of the last one goes. */
  reset(change: Partial<RunSnapshot> = {}): void {
    snapshot = { ...INITIAL, ...change };
    emit();
  },
  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
};

export function useRun(): RunSnapshot {
  return useSyncExternalStore(runStore.subscribe, runStore.get, runStore.get);
}
