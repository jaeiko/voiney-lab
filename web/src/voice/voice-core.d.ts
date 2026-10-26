// The names /voice-core.js and /connection.js give the page (lane RX2). They
// are classic scripts loaded at run time by loadVoiceScripts(), never bundled:
// their top-level `let`s are the page's global names, read and set here by
// name exactly as index.html's own script reads and sets them. Only the
// names this screen uses are declared.

/* eslint-disable no-var */
declare let socket: WebSocket | null | undefined;
declare let sessionActive: boolean;
declare let sessionGeneration: number;
declare let visibleState: string;
declare let pendingSessionConfiguration: Record<string, unknown> | null;
declare let acceptedSessionConfiguration: {
  configuration_id: number;
  server_generation: number;
  mode: string;
  language: string;
  input_language?: string;
  protocol_id: string | null;
  revision_id: string | null;
  experiment_session_id: string | null;
  experiment_session_version: number | null;
} | null;
declare let nextConfigurationId: number;
declare let activeTurn: number | null;
declare let activeCascadeGeneration: number | null;
declare let receiving: { turn_id: number; segment_index: number; generation?: number } | null;
declare let receivedFrames: ArrayBuffer[];
declare let receivingFiller: { turn_id: number; generation: number } | null;
declare let fillerFrames: ArrayBuffer[];
declare let audioComplete: boolean;
declare let turnComplete: boolean;
declare let playing: boolean;
declare let activeSource: unknown;
declare let nextSegment: number;
declare let lastReplayableAudio: ArrayBuffer | null;
declare let pendingVoiceReplay: boolean;
declare let micWorkletNode: unknown;
declare let playContext: AudioContext | null | undefined;
declare const queued: Map<number, ArrayBuffer>;
declare const interruptedCascadeTurns: Set<string>;
declare const audioRecoveryTurns: Set<string>;

declare function sendControl(type: string, fields?: Record<string, unknown>): void;
declare function init(generation?: number): Promise<void>;
declare function ensureAudio(generation: number): Promise<void>;
declare function closeAudio(): Promise<void>;
declare function clearPlayback(): void;
declare function pump(generation?: number): Promise<void>;
declare function join(frames: ArrayBuffer[]): ArrayBuffer;
declare function playFiller(data: ArrayBuffer, meta: unknown, generation?: number): Promise<void>;
declare function playFillerTone(meta: unknown, generation?: number): Promise<boolean>;
declare function playTimerNoticeTone(): boolean;
declare function clearFillerPlayback(turnId?: number | null, generation?: number | null): boolean;
declare function provisionalCascadeInterruption(message: unknown): boolean;
declare function rejectCascadeInterruption(message: unknown): boolean;
declare function interruptCascadePlayback(turnId: number, generation: number): boolean;
declare function cascadeTurnKey(turnId: number, generation: number): string;
declare function cascadeTurnInterrupted(message: unknown): boolean;
declare function currentCascadeMessage(message: unknown): boolean;
declare function primeRunStart(): void;
declare function restPrimedAudio(): boolean;
declare function resumeHeldAudio(): void;
declare function audioHeld(): boolean;
declare function keepScreenOn(on: boolean): Promise<boolean>;
declare function micStartError(error: unknown): unknown;
declare function serverNowMs(): number;
declare function formatElapsedClock(seconds: number): string;
declare function stepTimerRemainingSeconds(step: unknown): number;

declare var voineyConnection:
  | {
      watch(socket: WebSocket): void;
      lost(socket: WebSocket, reason: string): boolean;
      consume(message: unknown, socket: WebSocket): boolean;
      micText(): string;
      isLost(): boolean;
      reconnecting(): boolean;
      serverNow(): number;
    }
  | undefined;
