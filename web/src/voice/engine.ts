// The new screen's side of the shared voice code (lane RX2, person's decision
// 4 of 2026-10-25). /voice-core.js -- the socket, the microphone, the answer's
// audio, the server's clock and the screen lock, moved unchanged out of the
// old screen -- and /connection.js -- a lost connection seen and put back --
// are loaded as they are, as classic scripts, and this file is the "page"
// they were written beside: it defines the names they call
// (VOICE_CORE_HOST_NAMES, and connection.js's own), reads the session's
// events and tells the store what to draw.
//
// The event reading below is index.html's onMessage for the lines that move
// the audio, the turn fences and the session -- the same conditions, word for
// word where the old lines touch no card of the old screen
// (tests/test_lane_rx2_voice_core.py compares them) -- and the store's
// applyServerEvent for what the new screen draws. It decides no workflow
// state; the server does.
/* eslint-disable @typescript-eslint/no-explicit-any */
import { run as runCopy } from "../copy/ko";
import type { VoiceState } from "./runModel";
import { runStore } from "./runStore";

declare let playbackEpoch: number;
declare function resetCascadePlayback(): void;
declare function reconnectMic(generation?: number, reason?: string): Promise<boolean>;
declare function recordCalibrationPcm(seconds: number): Promise<{ pcm: Int16Array; micId: string | null }>;
declare function pcmBase64(pcm: Int16Array): string;

const VOICE_STATES = new Set<VoiceState>(["IDLE", "CONNECTING", "LISTENING", "THINKING", "SPEAKING", "INTERRUPTING", "RECONNECTING", "ERROR"]);
const RESUMABLE = ["ready", "in_progress", "paused", "blocked"];
// The per-turn records connection.js clears when it continues a run on a new
// connection (its forgetTurns); the new screen keeps the ones the fences read.
const TURN_MAPS = ["turns", "provisionalTurns", "pendingFillers", "toolEvents", "visualEvents", "researchEvents", "brainEvents", "webEvents", "drawingEvents", "turnProgress", "turnServerGenerations", "turnHoldEvidence", "runTurns"] as const;
const TURN_SETS = ["greetingEvents", "interruptedTurns"] as const;
// How long a stop waits for the server's session.stopped (index.html's STOP_ANSWER_MS).
export const STOP_ANSWER_MS = 3000;

const g = globalThis as any;
const TURN_FAILED_WORDS = runCopy.turnFailed;
let stopRequest: { socket: WebSocket | null | undefined; resolve: (() => void) | null } | null = null;

function turnBrowserKey(turnId: unknown, browserGeneration: number = sessionGeneration): string {
  return `${browserGeneration}:${String(turnId)}`;
}

/** A fixed element per name for the old screen's ids voice-core writes to; two of them tell the store. */
const sinks = new Map<string, unknown>();
function sink(id: string): unknown {
  if (sinks.has(id)) return sinks.get(id);
  let value: unknown;
  if (id === "audio-unlock") {
    // renderAudioUnlock: shown while the browser holds the sound back.
    value = { get hidden() { return !runStore.get().audioHeld; }, set hidden(on: boolean) { runStore.set({ audioHeld: !on }); } };
  } else if (id === "mic-recovery") {
    // showMicRecovery / hideMicRecovery: the microphone lost and not opened again.
    value = { get hidden() { return runStore.get().micProblem === null; }, set hidden(on: boolean) { if (on) runStore.set({ micProblem: null }); } };
  } else {
    value = typeof document !== "undefined" ? document.createElement("div") : { textContent: "", hidden: true };
  }
  sinks.set(id, value);
  return value;
}

function syncProtocolState(): void {
  const protocol = runStore.get().model.protocol;
  g.curatedProtocolState = protocol ? { ...protocol } : { attached: false };
  g.curatedStateRevision = runStore.get().model.stateRevision;
}

function eventContext() {
  return {
    filterWords: runCopy.filter,
    accepted: acceptedSessionConfiguration,
    live: (m: any) => !cascadeTurnInterrupted(m) && currentCascadeMessage(m) && Number.isInteger(m.turn_id),
    now: Date.now(),
  };
}

// The few turn checks the fences need (index.html's applyTurnState, without its card): a turn's states in order, none after its end.
const TERMINAL = new Set(["complete", "blocked", "cancelled", "error"]);
function applyTurnState(message: any, browserGeneration: number = sessionGeneration): boolean {
  const accepted = acceptedSessionConfiguration;
  if (!accepted || accepted.mode !== "cascade" || message.configuration_id !== accepted.configuration_id || !Number.isInteger(message.turn_id) || message.turn_id <= 0 || !Number.isInteger(message.generation) || message.generation < 0 || !Number.isInteger(message.revision) || message.revision <= 0) return false;
  const browserKey = turnBrowserKey(message.turn_id, browserGeneration);
  const knownGeneration = g.turnServerGenerations.get(browserKey);
  if (knownGeneration !== undefined && knownGeneration !== message.generation) return false;
  if (cascadeTurnInterrupted(message) && message.state !== "cancelled") return false;
  const key = `${message.generation}:${message.turn_id}`;
  const previous = g.turnProgress.get(key);
  if (previous && (message.revision <= previous.revision || TERMINAL.has(previous.state))) return false;
  g.turnServerGenerations.set(browserKey, message.generation);
  g.turnProgress.set(key, { revision: message.revision, state: message.state, terminal: TERMINAL.has(message.state) });
  return true;
}

// index.html's removeProvisionalTurn without its card: an utterance the server let go, whose words never came, stops the audio it had begun to stand for.
function removeProvisionalTurn(id: number, browserGeneration: number = sessionGeneration, serverGeneration: number | null = null): boolean {
  const key = turnBrowserKey(id, browserGeneration);
  const known = g.provisionalTurns.get(key);
  if (known !== undefined && serverGeneration !== null && known !== serverGeneration) return false;
  g.provisionalTurns.delete(key);
  if (Number.isInteger(serverGeneration)) g.pendingFillers.delete(cascadeTurnKey(id, serverGeneration as number));
  if (id === activeTurn && (serverGeneration === null || serverGeneration === activeCascadeGeneration)) {
    playbackEpoch++;
    resetCascadePlayback();
  }
  return known !== undefined;
}

function removeProvisionalTurns(browserGeneration: number = sessionGeneration): void {
  for (const [key, serverGeneration] of [...g.provisionalTurns]) {
    if (!String(key).startsWith(`${browserGeneration}:`)) continue;
    removeProvisionalTurn(Number(String(key).slice(String(key).indexOf(":") + 1)), browserGeneration, serverGeneration as number);
  }
}

function renderState(state: string, detail = ""): void {
  const known = VOICE_STATES.has(state as VoiceState) ? (state as VoiceState) : "ERROR";
  visibleState = known;
  runStore.set({ voice: known, detail });
}

function renderRunStatus(): void {
  runStore.set({ micOn: Boolean(micWorkletNode), audioHeld: Boolean(micWorkletNode) && audioHeld(), active: sessionActive === true });
}

function fail(error: unknown): void {
  const text = String((error as { message?: unknown })?.message || error);
  sessionActive = false;
  clearPlayback();
  void closeAudio();
  runStore.set({ phase: "idle", active: false });
  renderState("ERROR", text);
}

function invalidateCuratedProtocolState(): void {
  g.curatedProtocolState = { attached: false };
  g.curatedStateRevision = -1;
}

/** The names voice-core.js and connection.js reach for, defined once on this page. */
export function installVoiceHost(): void {
  if (g.__voineyVoiceHost) return;
  g.__voineyVoiceHost = true;
  for (const name of TURN_MAPS) g[name] = new Map();
  for (const name of TURN_SETS) g[name] = new Set();
  Object.assign(g, {
    runCardKey: null,
    curatedProtocolState: { attached: false },
    curatedStateRevision: -1,
    currentExperimentSessionId: null,
    currentExperimentSessionVersion: null,
    currentExperimentSessionStatus: null,
    RESUMABLE_EXPERIMENT_STATUSES: new Set(RESUMABLE),
    $: (id: string) => (typeof document !== "undefined" && document.getElementById(id)) || sink(id),
    setText: (id: string, value: string) => {
      const node = g.$(id);
      if (node) node.textContent = value;
      if (id === "mic-recovery-text") runStore.set({ micProblem: value ? String(value) : null });
    },
    setDisabled: (node: any, value: boolean) => {
      if (node) node.disabled = value;
    },
    announceMilestone: (text: string) => {
      const node = typeof document !== "undefined" ? document.getElementById("run-announcer") : null;
      if (node) node.textContent = text;
    },
    renderState,
    renderRunStatus,
    renderRunView: () => {
      syncProtocolState();
      renderRunStatus();
    },
    renderMicCheck: () => undefined,
    screenView: () => {
      const protocol = runStore.get().model.protocol;
      return sessionActive && protocol?.attached === true && protocol.active === true ? "during" : "before";
    },
    serverSocketUrl: (path = "/ws") => `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${path}`,
    onMessage,
    fail,
    recheckAccountAfterClose: () => {
      g.dispatchEvent?.(new Event("voiney-lab:account-check"));
      return Promise.resolve(false);
    },
    removeProvisionalTurns,
    invalidateCuratedProtocolState,
    stopWorkflowTimer: () => undefined,
    stopReportPolling: () => undefined,
    workspaceFetch: (url: string, options: RequestInit = {}) => fetch(url, { credentials: "same-origin", ...options }),
  });
}

let loading: Promise<void> | null = null;

function loadScript(src: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const existing = document.querySelector(`script[data-voiney-voice="${src}"]`);
    if (existing) {
      resolve();
      return;
    }
    const script = document.createElement("script");
    script.src = src;
    script.async = false;
    script.dataset.voineyVoice = src;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error(`${src} not loaded`));
    document.head.appendChild(script);
  });
}

/** voice-core.js, then connection.js, once per page, after this page's names are defined. */
export function loadVoiceScripts(): Promise<void> {
  if (!loading) {
    installVoiceHost();
    loading = loadScript("/voice-core.js").then(() => loadScript("/connection.js"));
    loading.catch(() => {
      loading = null;
    });
  }
  return loading;
}

/** index.html's onMessage, for what moves the audio, the fences and the session; then the store, for what the screen draws. */
async function onMessage(e: MessageEvent, generation: number, current: WebSocket): Promise<void> {
  if (generation !== sessionGeneration || socket !== current) return;
  if (typeof e.data !== "string") {
    if (receivingFiller) { fillerFrames.push(e.data); return; }
    if (receiving && receiving.turn_id === activeTurn) receivedFrames.push(e.data);
    return;
  }
  let m: any;
  try {
    m = JSON.parse(e.data);
  } catch {
    return;
  }
  if (voineyConnection?.consume?.(m, current)) return;
  if (m.type === "session.ready") {
    const pending: any = pendingSessionConfiguration, serverInputLanguage = m.input_language ?? m.language, pendingInputLanguage = pending?.input_language ?? pending?.language, revisionValid = pending?.protocol_id === null ? (m.revision_id === null || m.revision_id === undefined) : (typeof m.revision_id === "string" && m.revision_id.length > 0), valid = pending && Number.isInteger(m.configuration_id) && m.configuration_id === pending.configuration_id && m.mode === pending.mode && m.language === pending.language && serverInputLanguage === pendingInputLanguage && m.protocol_id === pending.protocol_id && revisionValid;
    if (!valid) return;
    if (m.generation !== undefined && (!Number.isInteger(m.generation) || m.generation < 0)) return;
    acceptedSessionConfiguration = { configuration_id: m.configuration_id, server_generation: Number.isInteger(m.generation) ? m.generation : 0, mode: m.mode, language: m.language, input_language: serverInputLanguage, protocol_id: m.protocol_id, revision_id: m.revision_id ?? null, experiment_session_id: typeof m.experiment_session_id === "string" ? m.experiment_session_id : null, experiment_session_version: Number.isInteger(m.experiment_session_version) ? m.experiment_session_version : null };
    pendingSessionConfiguration = null;
    if (acceptedSessionConfiguration.experiment_session_id && acceptedSessionConfiguration.experiment_session_version !== null) {
      g.currentExperimentSessionId = acceptedSessionConfiguration.experiment_session_id;
      g.currentExperimentSessionVersion = acceptedSessionConfiguration.experiment_session_version;
      runStore.set({ experimentId: acceptedSessionConfiguration.experiment_session_id });
    }
    try { await ensureAudio(generation); } catch (error) { sendControl("session.stop"); fail(micStartError(error)); return; }
    if (generation !== sessionGeneration || socket !== current || acceptedSessionConfiguration?.configuration_id !== m.configuration_id) return;
    sessionActive = true;
    runStore.set({ active: true, phase: "running" });
    renderState("LISTENING");
    renderRunStatus();
  }
  if (m.type === "session.greeting") {
    const accepted = acceptedSessionConfiguration, key = m.greeting_id; if (!accepted || accepted.mode !== "cascade" || m.configuration_id !== accepted.configuration_id || !Number.isInteger(m.turn_id) || m.turn_id <= 0 || !Number.isInteger(m.generation) || typeof key !== "string" || g.greetingEvents.has(key)) return; g.greetingEvents.add(key); activeTurn = m.turn_id; activeCascadeGeneration = m.generation; g.turnServerGenerations.set(turnBrowserKey(m.turn_id, generation), m.generation); renderState("THINKING");
  }
  if (m.type === "server.words" || m.type === "protocol.notice.speech") {
    const accepted = acceptedSessionConfiguration; if (!accepted || accepted.mode !== "cascade" || m.configuration_id !== accepted.configuration_id || !Number.isInteger(m.turn_id) || m.turn_id <= 0 || !Number.isInteger(m.generation) || typeof m.text !== "string") return; activeTurn = m.turn_id; activeCascadeGeneration = m.generation; g.turnServerGenerations.set(turnBrowserKey(m.turn_id, generation), m.generation); renderState("THINKING");
  }
  if (m.type === "session.configuration_required") {
    if (!pendingSessionConfiguration || m.configuration_id !== pendingSessionConfiguration.configuration_id) return;
    pendingSessionConfiguration = null; acceptedSessionConfiguration = null; sessionActive = false; restPrimedAudio(); removeProvisionalTurns(generation); invalidateCuratedProtocolState();
    const words = typeof m.message_ko === "string" && m.message_ko.trim() ? m.message_ko.trim() : "";
    runStore.set({ phase: "idle", active: false, startRefusal: { reason: typeof m.reason === "string" ? m.reason : "", words } });
    renderState("IDLE", words);
    return;
  }
  if (m.type === "session.stopped" && stopRequest?.socket === current) stopRequest.resolve?.();
  if (m.type === "session.stopped") { sessionActive = false; removeProvisionalTurns(generation); invalidateCuratedProtocolState(); runStore.set({ active: false }); renderState("IDLE"); }
  if (m.type === "barge_in_candidate") { provisionalCascadeInterruption(m); }
  if (m.type === "barge_in_rejected") { rejectCascadeInterruption(m); }
  if (m.type === "cascade.playback.clear") {
    const accepted = acceptedSessionConfiguration, key = cascadeTurnKey(m.turn_id, m.generation), valid = accepted && accepted.mode === "cascade" && m.configuration_id === accepted.configuration_id && Number.isInteger(m.turn_id) && Number.isInteger(m.generation) && Number.isInteger(m.superseding_turn_id) && Number.isInteger(m.superseding_generation) && m.reason === "confirmed_speech" && m.state === "cancelled";
    if (!valid || interruptedCascadeTurns.has(key) || m.turn_id !== activeTurn || m.generation !== activeCascadeGeneration || !applyTurnState(m, generation) || !interruptCascadePlayback(m.turn_id, m.generation)) return;
    interruptedCascadeTurns.add(key); g.pendingFillers.delete(key); if (sessionActive) renderState("INTERRUPTING");
  }
  if (m.type === "filler.audio.start") { const accepted = acceptedSessionConfiguration, valid = accepted && accepted.mode === "cascade" && m.configuration_id === accepted.configuration_id && currentCascadeMessage(m) && !cascadeTurnInterrupted(m) && Number.isInteger(m.frame_count) && m.frame_count > 0; if (valid && !playing && !activeSource) { receivingFiller = m; fillerFrames = []; } }
  if (m.type === "filler.audio.end" && receivingFiller && m.turn_id === receivingFiller.turn_id && m.generation === receivingFiller.generation) { const meta = receivingFiller, data = join(fillerFrames); receivingFiller = null; fillerFrames = []; await playFiller(data, meta, generation); }
  if (m.type === "filler.audio.clear") clearFillerPlayback(m.turn_id, m.generation);
  if (m.type === "filler.tone") { const accepted = acceptedSessionConfiguration, valid = accepted && accepted.mode === "cascade" && m.configuration_id === accepted.configuration_id && currentCascadeMessage(m) && !cascadeTurnInterrupted(m); if (valid) await playFillerTone(m, generation); }
  if (m.type === "state.changed" && sessionActive && !cascadeTurnInterrupted(m) && (!Number.isInteger(m.turn_id) || currentCascadeMessage(m)) && m.state !== "AGENT_SPEAKING") renderState(m.state === "PROCESSING" ? "THINKING" : "LISTENING");
  if (m.type === "turn.processing" && sessionActive && !cascadeTurnInterrupted(m) && (activeTurn === null || currentCascadeMessage(m))) renderState("THINKING");
  if (m.type === "speech.start") { activeTurn = m.turn_id; activeCascadeGeneration = Number.isInteger(m.generation) ? m.generation : null; if (Number.isInteger(m.generation)) { const key = turnBrowserKey(m.turn_id, generation), known = g.turnServerGenerations.get(key); if (known === undefined) g.turnServerGenerations.set(key, m.generation); g.provisionalTurns.set(key, m.generation); } }
  if (m.type === "speech.rejected") { removeProvisionalTurn(m.turn_id, generation, Number.isInteger(m.generation) ? m.generation : null); if (sessionActive) renderState("LISTENING"); }
  // Lane HEAR decision 3: a dropped sound is said ("잘 안 들렸어요", the store); index.html's line returns after it too.
  if (m.type === "speech.dropped") { runStore.event(m, eventContext()); return; }
  // Lane HEAR decision 7: the guidance playing stops with the pause.
  if (m.type === "playback.stop") { const accepted = acceptedSessionConfiguration; if (accepted && m.configuration_id === accepted.configuration_id) clearPlayback(); return; }
  if (m.type === "speech.ignored") { removeProvisionalTurn(m.turn_id, generation, Number.isInteger(m.generation) ? m.generation : null); if (sessionActive && m.during_playback !== true) renderState("LISTENING"); }
  if (m.type === "transcript" && !cascadeTurnInterrupted(m) && currentCascadeMessage(m)) { g.provisionalTurns.delete(turnBrowserKey(m.turn_id, generation)); }
  if (m.type === "turn.state") applyTurnState(m, generation);
  if (m.type === "audio.segment.start") { if (cascadeTurnInterrupted(m)) return; if (activeTurn === null) { activeTurn = m.turn_id; activeCascadeGeneration = Number.isInteger(m.generation) ? m.generation : null; } if (!currentCascadeMessage(m) || m.segment_index < nextSegment) return; clearFillerPlayback(m.turn_id, m.generation); receiving = m; receivedFrames = []; }
  if (m.type === "audio.segment.end" && !cascadeTurnInterrupted(m) && receiving && currentCascadeMessage(m) && m.segment_index === receiving.segment_index && (!Number.isInteger(m.generation) || m.generation === receiving.generation)) { queued.set(m.segment_index, join(receivedFrames)); receiving = null; receivedFrames = []; pump(generation); }
  if (m.type === "audio.complete" && !cascadeTurnInterrupted(m) && currentCascadeMessage(m)) { audioComplete = true; pump(generation); }
  if (m.type === "audio.replay.request" && !cascadeTurnInterrupted(m) && currentCascadeMessage(m)) { if (lastReplayableAudio) { pendingVoiceReplay = true; pump(generation); } }
  if (m.type === "server.operation" && !cascadeTurnInterrupted(m) && currentCascadeMessage(m) && m.operation === "audio_replay_request") audioRecoveryTurns.add(cascadeTurnKey(m.turn_id, m.generation));
  if (m.type === "turn.done" && !cascadeTurnInterrupted(m) && currentCascadeMessage(m)) { turnComplete = true; pump(generation); }
  if (m.type === "protocol.timer.notice" && acceptedSessionConfiguration && m.configuration_id === acceptedSessionConfiguration.configuration_id && m.chime === true) playTimerNoticeTone();
  if (m.type === "experiment.session.state" && m.state && typeof m.state.session_id === "string" && (!acceptedSessionConfiguration?.experiment_session_id || m.state.session_id === acceptedSessionConfiguration.experiment_session_id)) {
    g.currentExperimentSessionId = m.state.session_id; g.currentExperimentSessionVersion = Number.isInteger(m.state.version) ? m.state.version : null; if (typeof m.state.status === "string") g.currentExperimentSessionStatus = m.state.status;
    runStore.set({ experimentId: m.state.session_id });
  }
  if (m.type === "error") {
    const turnError = Number.isInteger(m.turn_id) && Number.isInteger(m.generation);
    const words = typeof m.message_ko === "string" && m.message_ko.trim() ? m.message_ko.trim() : "";
    if (m.recoverable === true) {
      if (pendingSessionConfiguration && !acceptedSessionConfiguration) {
        pendingSessionConfiguration = null;
        runStore.set({ phase: "idle", startRefusal: { reason: typeof m.code === "string" ? m.code : "", words } });
        renderState("IDLE", words);
      } else {
        runStore.event({ type: "workflow.control.refused", configuration_id: acceptedSessionConfiguration?.configuration_id, message_ko: words }, eventContext());
      }
      return;
    }
    if (turnError && sessionActive) {
      // A turn's own failure: the session goes on; the screen says the words were not taken (index.html writes it on the turn's card).
      runStore.event({ type: "workflow.control.refused", configuration_id: acceptedSessionConfiguration?.configuration_id, message_ko: words || TURN_FAILED_WORDS }, eventContext());
      return;
    }
    fail(words || "연결에 문제가 생겼어요.");
    return;
  }
  if (m.type === "protocol.fixture.state" && g.curatedStateRevision === -1 && runStore.get().model.stateRevision > -1) {
    // connection.js continued the run on a new connection: its first state is taken whatever the old one's number was.
    runStore.set({ model: { ...runStore.get().model, stateRevision: -1 } });
  }
  runStore.event(m, eventContext());
  if (m.type === "protocol.fixture.state") syncProtocolState();
}

export interface StartRequest {
  protocolId: string;
  changeSetId?: string | null;
  resume?: { sessionId: string; version: number } | null;
}

/** The start press, in the touch itself: the sound made and resumed and the screen kept on (voice-core's primeRunStart), before anything is awaited. */
export function primeStart(): void {
  if (typeof g.primeRunStart === "function") primeRunStart();
}

/** index.html's startSession for this screen's start: one session.start on the socket, opened if it is not. */
export async function startRun(request: StartRequest): Promise<void> {
  await loadVoiceScripts();
  const generation = sessionGeneration;
  sessionActive = false;
  acceptedSessionConfiguration = null;
  for (const name of TURN_MAPS) g[name].clear();
  for (const name of TURN_SETS) g[name].clear();
  invalidateCuratedProtocolState();
  const configuration_id = ++nextConfigurationId;
  const pending = {
    configuration_id, mode: "cascade", language: "ko", protocol_id: request.protocolId,
    ...(request.resume ? { experiment_session_id: request.resume.sessionId, experiment_session_version: request.resume.version } : {}),
    ...(!request.resume && request.changeSetId ? { run_change_set_id: request.changeSetId } : {}),
  };
  pendingSessionConfiguration = pending;
  runStore.reset({ phase: "starting", startedProtocolId: request.protocolId });
  renderState("CONNECTING");
  try {
    if (!socket || socket.readyState !== WebSocket.OPEN) await init(generation);
    if (generation !== sessionGeneration || pendingSessionConfiguration !== pending) return;
    sendControl("session.start", pending);
  } catch (error) {
    if (generation === sessionGeneration) fail(error);
  }
}

/** index.html's stopSession: session.stop and its answer awaited a moment, then the session let go and the socket closed. */
export async function stopRun(): Promise<void> {
  if (stopRequest || typeof g.sendControl !== "function") return;
  const wasActive = sessionActive, current = socket;
  runStore.set({ phase: "stopping" });
  if (wasActive && current?.readyState === WebSocket.OPEN) {
    sessionActive = false;
    const request: { socket: WebSocket | null | undefined; resolve: (() => void) | null } = { socket: current, resolve: null };
    const answered = new Promise<void>((resolve) => { request.resolve = resolve; });
    stopRequest = request;
    sendControl("session.stop");
    let timer: ReturnType<typeof setTimeout> | undefined;
    await Promise.race([answered, new Promise<void>((resolve) => { timer = setTimeout(resolve, STOP_ANSWER_MS); })]);
    clearTimeout(timer);
    stopRequest = null;
  }
  sessionActive = false;
  removeProvisionalTurns(sessionGeneration);
  sessionGeneration++;
  pendingSessionConfiguration = null;
  acceptedSessionConfiguration = null;
  clearPlayback();
  invalidateCuratedProtocolState();
  const oldSocket = socket;
  socket = null;
  await closeAudio();
  if (oldSocket && oldSocket.readyState < WebSocket.CLOSING) oldSocket.close(1000, "session stopped");
  void keepScreenOn(false);
  runStore.set({ phase: "idle", active: false });
  renderState("IDLE");
}

/** index.html's togglePauseSession: the server pauses or resumes; the screen shows what its state then says. */
export function togglePause(paused: boolean): boolean {
  if (!sessionActive || !socket || socket.readyState !== WebSocket.OPEN) return false;
  socket.send(JSON.stringify({ type: paused ? "workflow.resume" : "workflow.pause", configuration_id: acceptedSessionConfiguration?.configuration_id, generation: acceptedSessionConfiguration?.server_generation }));
  return true;
}

/** index.html's chooseClarifyCandidate: a candidate the last reply asked back, tapped instead of said. */
export function chooseCandidate(turnId: number, name: string): boolean {
  const accepted = acceptedSessionConfiguration;
  if (!sessionActive || !accepted || socket?.readyState !== WebSocket.OPEN || !Number.isInteger(turnId) || !name.trim()) return false;
  sendControl("client.clarify_choice", { configuration_id: accepted.configuration_id, generation: accepted.server_generation, turn_id: turnId, candidate: name });
  return true;
}

/** A touch: the sound the browser held back resumed (voice-core's resumeHeldAudio). */
export function resumeSound(): void {
  if (typeof g.resumeHeldAudio === "function") resumeHeldAudio();
}

/** The microphone opened again by hand after it was lost (voice-core's reconnectMic). */
export function reconnectMicrophone(): void {
  if (typeof g.reconnectMic === "function") void reconnectMic(sessionGeneration, "manual");
}

/** The server's clock as connection.js measures it (voice-core's serverNowMs), or this page's before the scripts load. */
export function serverNow(): number {
  return typeof g.serverNowMs === "function" ? serverNowMs() : Date.now();
}

/** 목소리 맞추기's recording (lane HEAR's recordCalibrationPcm in voice-core): the run's microphone, 16 kHz PCM16, and its short id. */
export async function recordVoice(seconds: number): Promise<{ pcm: Int16Array; micId: string | null }> {
  await loadVoiceScripts();
  return recordCalibrationPcm(seconds);
}

/** The recording as the server takes it (voice-core's pcmBase64). */
export function encodePcm(pcm: Int16Array): string {
  return pcmBase64(pcm);
}

