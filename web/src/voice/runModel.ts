// What the experiment screen shows, read from the server's socket events
// (lane RX2, 2026-10-25). Everything here is the server's: the step, the
// timers, the pause, the question, what was heard and said. The screen keeps
// no workflow state of its own (AGENTS.md rule 2); it draws the last state
// the server sent. Pure functions, so they are tested without a socket.

export type VoiceState =
  | "IDLE"
  | "CONNECTING"
  | "LISTENING"
  | "THINKING"
  | "SPEAKING"
  | "INTERRUPTING"
  | "RECONNECTING"
  | "ERROR";

export interface StepTimer {
  state: string;
  name?: string | null;
  duration_seconds?: number | null;
  deadline_at?: string | null;
  remaining_seconds?: number | null;
  elapsed_seconds?: number | null;
  bound?: string | null;
}

export interface ProtocolState {
  attached: boolean;
  protocol_id: string;
  revision_id: string;
  source_sha256?: string;
  display_name: string;
  development_only: boolean;
  active: boolean;
  total_steps: number;
  at_final_step: boolean;
  current_step_label: string | null;
  current_step_id: string | null;
  primary_summary?: string | null;
  display_summary?: string | null;
  workflow_status?: string;
  block_reason: string | null;
  revision: number;
  source_page_refs?: number[];
  visual_assets?: unknown[];
  visual_status?: string;
  timers?: { step?: StepTimer | null; experiment?: Record<string, unknown> | null; pause?: Record<string, unknown> | null };
  current_step_run_changes?: Array<{ kind_ko?: string; words_ko?: string }> | null;
}

export interface ScreenFields {
  next_step_sentence?: string | null;
  open_question?: { kind?: string; text?: string } | null;
  confirm_question?: { kind?: string } | null;
  clarify_candidates?: string[];
  readback_values?: string[];
  repeat_round?: { words?: string } | null;
  source_figures?: unknown[] | null;
  [key: string]: unknown;
}

export interface Figure {
  key: string;
  url: string;
  label: string;
  caption: string;
  /** Where the picture comes from, said beside it: the source, or drawn by the AI from the source's words, or the web. */
  origin: "source" | "drawn" | "web";
}

export interface LogLine {
  key: string;
  who: "heard" | "said";
  text: string;
}

export interface Card {
  turnId: number;
  kind: "confirm" | "clarify" | "record" | "answer";
  text: string;
  candidates: string[];
  values: string[];
  stepId: string | null;
}

export interface RunModel {
  configurationId: number | null;
  protocol: ProtocolState | null;
  screen: ScreenFields | null;
  stateRevision: number;
  caption: LogLine | null;
  log: LogLine[];
  card: Card | null;
  figures: { drawn: Figure | null; web: Figure | null };
  /** When the server last dropped what it heard (shown for a few seconds as "잘 안 들렸어요"). */
  misheardAt: number | null;
  timerNotice: { text: string; stepId: string | null } | null;
  ended: { status: string | null; report: string | null } | null;
  /** A refusal said in the server's own words (a pause refused, a start refused). */
  notice: string | null;
}

export const EMPTY_RUN: RunModel = {
  configurationId: null,
  protocol: null,
  screen: null,
  stateRevision: -1,
  caption: null,
  log: [],
  card: null,
  figures: { drawn: null, web: null },
  misheardAt: null,
  timerNotice: null,
  ended: null,
  notice: null,
};

/** The server's discard signals: an utterance taken for another person's voice (lane SP1). Lane HEAR's own signal joins this set when it lands. */
export const MISHEARD_EVENTS = new Set(["speech.ignored"]);
export const MISHEARD_SHOWN_MS = 4000;
export const LOG_LIMIT = 200;

// The operations whose answer is a card under the step (the same table as index.html's RUN_CARD_KINDS).
const CARD_KINDS: Record<string, Card["kind"] | null> = {
  completion_confirmation_required: "confirm",
  learning_next_preview_confirmation: "confirm",
  reference_clarification_required: "clarify",
  parameter_clarification_required: "clarify",
  transcript_retry_required: "clarify",
  experiment_observation_recorded: "record",
  experiment_anomaly_recorded: "record",
  experiment_record_corrected: "record",
  branch_repeat_recorded: "record",
};
const NO_CARD = new Set([
  "protocol_start", "current_step_read", "current_step_repeat", "next_step_transition", "completion_and_next_transition",
  "completion_confirmation_declined", "workflow_paused", "workflow_resumed", "step_timer_started", "protocol_stop",
  "audio_replay_request", "readonly_operation_cancelled", "inactive_session_guard",
]);

const PRESENTATION_GLYPHS: Record<string, string> = { "": "(", "": ")", "": "-", "": ":" };

/** The server's private glyphs as plain characters (index.html's normalizePresentationText). */
export function plain(value: unknown): string {
  return String(value ?? "").replace(/[]/g, (glyph) => PRESENTATION_GLYPHS[glyph] ?? glyph);
}

function text(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function oneLine(value: unknown): string {
  return plain(value).replace(/\s+/g, " ").trim();
}

/** The words of a reply, as index.html's runReplyText reads them: the display document's sections, else the text. */
export function replyText(m: Record<string, unknown>): string {
  const document = m.display_document as { sections?: Array<{ kind?: string; text?: string; items?: unknown[] }> } | undefined;
  const sections = Array.isArray(document?.sections) ? document!.sections : null;
  if (sections && sections.length) {
    const parts: string[] = [];
    for (const section of sections) {
      if (!section || ["notice", "source", "citation"].includes(String(section.kind))) continue;
      if (section.kind === "bullets" && Array.isArray(section.items)) parts.push(...section.items.filter((item): item is string => typeof item === "string"));
      else if (typeof section.text === "string") parts.push(section.text);
    }
    const joined = parts.join("\n").trim();
    if (joined) return plain(joined);
  }
  const raw = typeof m.primary_text === "string" && m.primary_text ? m.primary_text : typeof m.text === "string" ? m.text : "";
  return plain(raw).replace(/^#+\s+/gm, "").trim();
}

/** A protocol state the screen may draw: the accepted configuration's, attached, of the accepted protocol and revision, not older than the one drawn. */
export function acceptableState(
  m: Record<string, unknown>,
  accepted: { configuration_id: number; protocol_id: string | null; revision_id: string | null } | null,
  lastRevision: number,
): ProtocolState | null {
  const value = m.state as ProtocolState | undefined;
  if (!accepted || m.configuration_id !== accepted.configuration_id || !value || typeof value !== "object") return null;
  if (value.attached !== true || value.protocol_id !== accepted.protocol_id || value.revision_id !== accepted.revision_id) return null;
  if (!text(value.display_name) || typeof value.active !== "boolean" || !Number.isInteger(value.total_steps) || value.total_steps <= 0) return null;
  if (!Number.isInteger(value.revision) || value.revision < lastRevision) return null;
  return value;
}

function figureOrigin(protocol: ProtocolState, value: Record<string, unknown>, url: string): boolean {
  return value.protocol_id === protocol.protocol_id && value.revision_id === protocol.revision_id && typeof value.url === "string" && value.url === url;
}

/** The step's pictures from the source: the figures cut from it, else the one asset the step names (index.html's safeSourceFigure and safeVisualAsset). */
export function sourceFigures(protocol: ProtocolState | null, screen: ScreenFields | null): Figure[] {
  if (!protocol) return [];
  const base = `/api/protocols/${encodeURIComponent(protocol.protocol_id)}/revisions/${encodeURIComponent(protocol.revision_id)}`;
  const shown: Figure[] = [];
  const figures = Array.isArray(screen?.source_figures) ? (screen!.source_figures as Array<Record<string, unknown>>) : [];
  for (const item of figures) {
    if (!item || item.kind !== "source_figure" || typeof item.figure_id !== "string" || !/^source-figure-[1-9][0-9]*-[1-9][0-9]*$/.test(item.figure_id)) continue;
    if (!["image/png", "image/jpeg"].includes(String(item.mime_type)) || !text(item.label)) continue;
    if (!figureOrigin(protocol, item, `${base}/figures/${encodeURIComponent(item.figure_id)}`)) continue;
    shown.push({ key: `figure:${item.figure_id}`, url: String(item.url), label: plain(item.label), caption: plain(item.caption ?? ""), origin: "source" });
  }
  if (shown.length) return shown;
  const assets = Array.isArray(protocol.visual_assets) ? (protocol.visual_assets as Array<Record<string, unknown>>) : [];
  if (assets.length === 1) {
    const asset = assets[0];
    const id = typeof asset?.asset_id === "string" ? asset.asset_id : "";
    if (
      asset && ["source_crop", "full_source_page_preview"].includes(String(asset.kind)) && /^(?:source-crop-[1-9][0-9]*-[1-9][0-9]*|source-page-[1-9][0-9]*)$/.test(id) &&
      ["image/png", "image/jpeg"].includes(String(asset.mime_type)) && text(asset.alt_text) && figureOrigin(protocol, asset, `${base}/assets/${encodeURIComponent(id)}`)
    ) {
      const label = asset.kind === "full_source_page_preview" ? `원문 ${String(asset.source_page)}쪽` : plain(asset.label ?? asset.alt_text);
      shown.push({ key: `asset:${id}`, url: String(asset.url), label, caption: plain(asset.caption_primary ?? asset.alt_text ?? ""), origin: "source" });
    }
  }
  return shown;
}

function cardFor(model: RunModel, turnId: number, operation: string): Card | null {
  if (NO_CARD.has(operation)) return null;
  const kind = Object.prototype.hasOwnProperty.call(CARD_KINDS, operation) ? CARD_KINDS[operation] : "answer";
  if (!kind) return null;
  return { turnId, kind, text: "", candidates: [], values: [], stepId: model.protocol?.current_step_id ?? null };
}

function withLog(model: RunModel, line: LogLine): RunModel {
  const log = model.log.filter((item) => item.key !== line.key);
  log.push(line);
  return { ...model, log: log.slice(-LOG_LIMIT), caption: line };
}

export interface EventContext {
  accepted: { configuration_id: number; protocol_id: string | null; revision_id: string | null } | null;
  /** The turn the audio plays now and whether a message belongs to it (voice-core's currentCascadeMessage), and whether it was cut off. */
  live: (m: Record<string, unknown>) => boolean;
  now: number;
}

/** One server event onto the model; events that change nothing return the same object. */
export function applyServerEvent(model: RunModel, m: Record<string, unknown>, context: EventContext): RunModel {
  const accepted = context.accepted;
  const ours = accepted !== null && m.configuration_id === accepted.configuration_id;
  const turnId = Number.isInteger(m.turn_id) ? (m.turn_id as number) : null;
  switch (m.type) {
    case "protocol.fixture.state": {
      const value = acceptableState(m, accepted, model.stateRevision);
      if (!value) return model;
      const screen = m.screen && typeof m.screen === "object" && !Array.isArray(m.screen) ? (m.screen as ScreenFields) : null;
      const stepChanged = model.protocol?.current_step_id !== value.current_step_id;
      let card = model.card;
      if (card && turnId === card.turnId && screen) {
        card = {
          ...card,
          candidates: card.kind === "clarify" && Array.isArray(screen.clarify_candidates) ? screen.clarify_candidates.filter(text).map(oneLine) : card.candidates,
          values: card.kind === "record" && Array.isArray(screen.readback_values) ? screen.readback_values.filter(text).map(oneLine) : card.values,
        };
      }
      if (stepChanged) card = null;
      return {
        ...model,
        configurationId: accepted!.configuration_id,
        protocol: value,
        screen,
        stateRevision: Math.max(model.stateRevision, value.revision),
        card,
        figures: stepChanged ? { drawn: null, web: null } : model.figures,
        timerNotice: stepChanged ? null : model.timerNotice,
      };
    }
    case "session.greeting":
    case "server.words":
    case "protocol.notice.speech":
      if (!ours || typeof m.text !== "string" || !m.text.trim()) return model;
      return withLog(model, { key: `said:${String(m.generation)}:${String(m.turn_id)}`, who: "said", text: oneLine(m.text) });
    case "transcript":
      if (turnId === null || !context.live(m) || !text(m.text)) return model;
      return withLog(model, { key: `heard:${String(m.generation)}:${turnId}`, who: "heard", text: oneLine(m.text) });
    case "reply.delta":
    case "reply.complete": {
      if (turnId === null || !context.live(m)) return model;
      const words = replyText(m);
      if (!words) return model;
      const key = `said:${String(m.generation)}:${turnId}`;
      const before = model.log.find((item) => item.key === key);
      const joined = m.type === "reply.complete" || !before ? words : `${before.text} ${words}`.trim();
      const next = withLog(model, { key, who: "said", text: oneLine(joined) });
      if (next.card && next.card.turnId === turnId) next.card = { ...next.card, text: plain(joined) };
      return next;
    }
    case "server.operation":
      if (turnId === null || !context.live(m) || m.route !== "curated_protocol" || typeof m.operation !== "string") return model;
      return { ...model, card: cardFor(model, turnId, m.operation) };
    case "protocol.timer.notice":
      if (!ours || typeof m.text !== "string") return model;
      return { ...model, timerNotice: { text: oneLine(m.text), stepId: typeof m.step_id === "string" ? m.step_id : null } };
    case "protocol.drawing.result": {
      const diagram = m.diagram as Record<string, unknown> | undefined;
      if (!ours || m.status !== "success" || !diagram || typeof diagram.asset_id !== "string" || !/^[0-9a-f]{64}$/.test(diagram.asset_id)) return model;
      if (diagram.url !== `/api/drawn-visuals/${diagram.asset_id}` || diagram.mime_type !== "image/svg+xml" || diagram.step_id !== model.protocol?.current_step_id || !text(diagram.label)) return model;
      return { ...model, figures: { ...model.figures, drawn: { key: `drawn:${diagram.asset_id}`, url: diagram.url as string, label: plain(diagram.label), caption: "", origin: "drawn" } } };
    }
    case "experiment.ended":
      if (!ours) return model;
      return { ...model, ended: { status: typeof m.status === "string" ? m.status : null, report: typeof m.report === "string" ? m.report : null } };
    case "workflow.control.refused":
      if (!ours) return model;
      return { ...model, notice: text(m.message_ko) ? plain(m.message_ko) : null };
    default:
      if (typeof m.type === "string" && MISHEARD_EVENTS.has(m.type) && (m.configuration_id === undefined || ours)) return { ...model, misheardAt: context.now };
      return model;
  }
}

/** The step timer as the screen shows it: none, running, its last minute, or ended (index.html's renderRunTimer). */
export interface TimerView {
  kind: "none" | "idle" | "running" | "soon" | "done";
  seconds: number;
  duration: number;
  name: string | null;
}

export function timerView(protocol: ProtocolState | null, remaining: (step: StepTimer) => number): TimerView {
  const step = protocol?.attached ? protocol.timers?.step ?? null : null;
  const duration = Math.max(0, Number(step?.duration_seconds) || 0);
  const name = step && text(step.name) ? plain(step.name) : null;
  if (step && (step.state === "running" || step.state === "expired")) {
    const seconds = remaining(step);
    if (seconds > 0) return { kind: seconds <= 60 ? "soon" : "running", seconds, duration, name };
    return { kind: "done", seconds: -seconds, duration, name };
  }
  if (step && step.state === "not_started" && duration > 0) return { kind: "idle", seconds: duration, duration, name };
  return { kind: "none", seconds: 0, duration, name };
}

/** The pause, as the server's state says it. */
export function isPaused(protocol: ProtocolState | null): boolean {
  return protocol?.attached === true && protocol.workflow_status === "paused";
}

/** The one listening word, large: the pause first, then a word the server just dropped, then the voice state. */
export type ListeningWord = "paused" | "misheard" | "listening" | "thinking" | "speaking" | "connecting" | "reconnecting" | "problem" | "idle";

export function listeningWord(voice: VoiceState, paused: boolean, misheardAt: number | null, now: number): ListeningWord {
  if (voice === "RECONNECTING") return "reconnecting";
  if (paused) return "paused";
  if (misheardAt !== null && now - misheardAt < MISHEARD_SHOWN_MS && voice === "LISTENING") return "misheard";
  switch (voice) {
    case "LISTENING":
      return "listening";
    case "THINKING":
      return "thinking";
    case "SPEAKING":
    case "INTERRUPTING":
      return "speaking";
    case "CONNECTING":
      return "connecting";
    case "ERROR":
      return "problem";
    default:
      return "idle";
  }
}
