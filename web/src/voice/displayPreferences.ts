import { useSyncExternalStore } from "react";

export type DisplayMode = "normal" | "far";

const STORAGE_KEY = "voiney-lab.display-mode";
const listeners = new Set<() => void>();

function storedMode(): DisplayMode {
  try {
    return localStorage.getItem(STORAGE_KEY) === "far" ? "far" : "normal";
  } catch {
    return "normal";
  }
}

let mode: DisplayMode = storedMode();

function notify(): void {
  for (const listener of listeners) listener();
}

export function setDisplayMode(next: DisplayMode): void {
  if (mode === next) return;
  mode = next;
  try {
    localStorage.setItem(STORAGE_KEY, next);
  } catch {
    // The mode still applies to this tab when storage is unavailable.
  }
  notify();
}

export function displayMode(): DisplayMode {
  return mode;
}

export function displayModeEvent(
  message: Record<string, unknown>,
  current: { configurationId: number | null; turnId: number | null; generation: number | null },
): DisplayMode | null {
  if (
    message.type !== "screen.display_mode"
    || (message.mode !== "normal" && message.mode !== "far")
    || !Number.isInteger(current.configurationId)
    || message.configuration_id !== current.configurationId
    || !Number.isInteger(current.turnId)
    || message.turn_id !== current.turnId
    || !Number.isInteger(current.generation)
    || message.generation !== current.generation
  ) return null;
  return message.mode;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useDisplayMode(): DisplayMode {
  return useSyncExternalStore(subscribe, displayMode, () => "normal");
}
