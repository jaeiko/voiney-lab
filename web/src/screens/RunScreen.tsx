import * as Dialog from "@radix-ui/react-dialog";
import { AudioLines, CircleAlert, Ear, Hourglass, Languages, Loader, Mic, MicOff, Pause, Play, Square, Timer, Volume2 } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";

import { EmptyBox, LoadingBox, Note } from "../components/Pieces";
import { run as copy } from "../copy/ko";
import { routes } from "../lib/navigation";
import { chooseCandidate, loadVoiceScripts, reconnectMicrophone, resumeSound, serverNow, stopRun, togglePause } from "../voice/engine";
import { FILTER_NOTICE_SHOWN_MS, isPaused, listeningWord, MISHEARD_SHOWN_MS, nextStepWords, sourceFigures, stepLabelWords, stepWords, timerView, type ListeningWord, type ProtocolState, type ScreenFields, type TimerView } from "../voice/runModel";
import { useRun } from "../voice/runStore";
import { useDisplayMode } from "../voice/displayPreferences";
import { CaptionBar } from "./run/CaptionBar";
import { EndScreen } from "./run/EndScreen";
import { FigurePanel } from "./run/FigurePanel";
import type { AuthSession } from "../api/types";
import { RunTools } from "./run/RunTools";
import { RecordsPanel } from "./run/RecordsPanel";

// The experiment screen (lane RX2, person's decision 2 of 2026-10-25; laid out
// again by lane UI1, person's request of 2026-10-31): on a wide screen (a
// landscape tablet, a desktop) two halves -- on the left the step (Korean
// first, its conditions, the next step, a card, the source folded) with the
// records folded under it ([메모] and [사진] always on the screen); on the
// right the timer in the top third (a list when there are several) and the
// pictures below it (the source's figures, a web photo, a drawn diagram, each
// named by where it comes from; one line when there is none). On a narrow
// screen (a portrait tablet, a phone) one column: the step, the timer right
// under it, then the pictures. The caption in two whole lines opens the
// conversation, the one listening word is large, and [일시정지] / [종료]. While
// paused a band across the whole screen says how to go on. With no experiment
// there is no run bar. Everything drawn is the server's last state
// (runModel.ts).

const NARROW = "(max-width: 899px)";
const MIC_NOTICE_SHOWN_MS = 10_000;
// The step the start flow opens at when the server refused a start for it (index.html's START_REFUSAL_STEPS).
const REFUSAL_STEPS: Record<string, number> = { start_check_required: 2, safety_acknowledgement_required: 3, start_check_unavailable: 1, run_changes_stale: 4, run_changes_required: 4 };

function useNarrow(): boolean {
  const query = typeof window !== "undefined" && typeof window.matchMedia === "function" ? window.matchMedia(NARROW) : null;
  const [narrow, setNarrow] = useState(() => query?.matches ?? false);
  useEffect(() => {
    if (!query) return;
    const update = () => setNarrow(query.matches);
    query.addEventListener?.("change", update);
    return () => query.removeEventListener?.("change", update);
  }, [query]);
  return narrow;
}

function useClock(ms: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const handle = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(handle);
  }, [ms]);
  return now;
}

function clock(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(total / 3600), minutes = Math.floor((total % 3600) / 60), secs = total % 60;
  return hours > 0 ? `${hours}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}` : `${minutes}:${String(secs).padStart(2, "0")}`;
}

/** Seconds left on the step timer, on the server's clock (index.html's stepTimerRemainingSeconds). */
function remainingOf(step: { deadline_at?: string | null; state: string; elapsed_seconds?: number | null; duration_seconds?: number | null; remaining_seconds?: number | null }): number {
  const deadline = Date.parse(step.deadline_at ?? "");
  if (Number.isFinite(deadline)) return Math.ceil((deadline - serverNow()) / 1000);
  if (step.state === "expired") return -Math.max(0, (Number(step.elapsed_seconds) || 0) - (Number(step.duration_seconds) || 0));
  return Math.max(0, Number(step.remaining_seconds) || 0);
}

const LISTENING_ICONS: Record<ListeningWord, typeof Ear> = {
  listening: Ear,
  thinking: Hourglass,
  speaking: Volume2,
  misheard: CircleAlert,
  paused: Pause,
  connecting: Loader,
  reconnecting: Loader,
  problem: CircleAlert,
  idle: AudioLines,
};

function ListeningBadge({ word, misheard }: { word: ListeningWord; misheard?: { notice: string | null; reason: string | null } }) {
  const Icon = LISTENING_ICONS[word];
  // Lane HEAR2, decision 4: a dropped sound shows the server's notice and its reason; nothing is said.
  const label = word === "misheard" && misheard?.notice ? misheard.notice : copy.listening[word];
  const hint = word === "misheard" ? misheard?.reason || copy.misheardHint : null;
  return (
    <div className="run-listening" data-word={word} role="status" aria-live="polite" data-testid="run-listening">
      <Icon aria-hidden="true" />
      <span>{label}</span>
      {hint ? <span className="run-listening-hint">{hint}</span> : null}
    </div>
  );
}

/**
 * The timers, at the top of the right half (wide) or right under the step (narrow), each large: its name, its digits,
 * its bar. The server sends one step timer today; several are listed one under another. With none, a wide screen says
 * so in one line and a narrow one leaves the space to the pictures.
 */
function TimerZone({ timers, narrow }: { timers: TimerView[]; narrow: boolean }) {
  const shown = timers.filter((timer) => timer.kind !== "none");
  if (!shown.length && narrow) return null;
  return (
    <section className="run-timers" aria-label={copy.timer.title} data-count={shown.length} data-testid="run-timers">
      {shown.length ? (
        <ul className="run-timers-list">
          {shown.map((timer, at) => (
            <li key={`${timer.name ?? "step"}:${at}`}>
              <TimerChip timer={timer} />
            </li>
          ))}
        </ul>
      ) : (
        <p className="run-timers-empty" data-testid="run-timers-empty">
          <Timer aria-hidden="true" />
          <span>{copy.timer.none}</span>
        </p>
      )}
    </section>
  );
}

function TimerChip({ timer }: { timer: TimerView }) {
  if (timer.kind === "none") return null;
  const big = timer.kind === "soon" || timer.kind === "done";
  const words = timer.kind === "done" ? copy.timer.over(clock(timer.seconds)) : timer.kind === "idle" ? copy.timer.idle(clock(timer.seconds)) : timer.kind === "paused" ? copy.timer.paused(clock(timer.seconds)) : copy.timer.left(clock(timer.seconds));
  const fill = timer.kind === "running" || timer.kind === "soon" || timer.kind === "paused" ? (timer.duration > 0 ? Math.min(100, (timer.seconds / timer.duration) * 100) : 0) : timer.kind === "idle" ? 100 : 0;
  return (
    <div className="run-timer" data-kind={timer.kind} data-big={big} role="timer" aria-live={big ? "assertive" : "off"} data-testid="run-timer">
      <span className="run-timer-name">
        <Timer aria-hidden="true" />
        {copy.timer.name(timer.name)}
      </span>
      <span className="run-timer-digits">{timer.kind === "done" ? clock(timer.seconds) : clock(timer.seconds)}</span>
      <span className="run-timer-words">{words}</span>
      <span className="run-timer-bar" aria-hidden="true">
        <span style={{ width: `${Math.round(fill)}%` }} />
      </span>
    </div>
  );
}

function StepPanel({ protocol, screen, children }: { protocol: ProtocolState | null; screen: ScreenFields | null; children?: ReactNode }) {
  // A step is shown by its id: a protocol whose steps carry no number (a source without numbering) still has steps.
  if (!protocol || !protocol.current_step_id) {
    return (
      <section className="run-step" data-testid="run-step">
        <p className="run-step-text run-step-waiting">{copy.waiting}</p>
      </section>
    );
  }
  // Person's decision 2 (2026-10-26): the Korean large and bold, the source small under it and folded; a step with no
  // Korean shows the source at an ordinary size -- never large -- and "번역 준비 중" when the server says the Korean is
  // missing (lane KO2's translation_missing). The server sends the Korean without its own "N단계:" (decision 5).
  const words = stepWords(protocol);
  const body = words.korean ?? words.source ?? "";
  const length = body.length > 120 ? "long" : body.length > 60 ? "medium" : "short";
  const label = protocol.current_step_label?.trim() ? protocol.current_step_label.trim() : null;
  const nextLabel = typeof screen?.next_step_label === "string" && screen.next_step_label.trim() ? screen.next_step_label.trim() : null;
  const nextSentence = typeof screen?.next_step_sentence === "string" ? screen.next_step_sentence : null;
  const next = nextSentence ? nextStepWords(nextSentence, screen?.next_step_translation_missing === true, nextLabel) : null;
  const translationStatus = typeof screen?.translation_status === "string" ? screen.translation_status : (words.missing && screen?.translation_pending === true ? "pending" : words.missing ? "missing" : "available");
  const nextTranslationStatus = typeof screen?.next_step_translation_status === "string" ? screen.next_step_translation_status : (next?.missing && screen?.translation_pending === true ? "pending" : next?.missing ? "missing" : "available");
  const translationNotice = (status: string) => status === "rejected" ? copy.translationRejected : status === "pending" ? copy.translationPending : copy.translationMissing;
  // The Korean is being made now; a translation_update will draw it (lane KO2).
  const busy = (words.missing && translationStatus === "pending") || (next?.missing === true && nextTranslationStatus === "pending");
  const changes = (protocol.current_step_run_changes ?? []).filter((item) => item?.words_ko);
  return (
    <section className="run-step" aria-labelledby="run-step-label" aria-busy={busy || undefined} data-testid="run-step">
      <p className="run-step-label" id="run-step-label">{label ? `${stepLabelWords(label)} · 전체 ${protocol.total_steps}단계` : copy.stepUnnumbered(protocol.total_steps)}</p>
      {words.missing ? (
        <p className="run-step-pending" data-testid="run-step-pending">
          <Languages aria-hidden="true" />
          <span>{translationNotice(translationStatus)}</span>
        </p>
      ) : null}
      <p className="run-step-text" data-length={length} data-lang={words.korean ? "ko" : "source"} lang={words.korean ? undefined : "en"} data-testid="run-step-text">
        {body}
      </p>
      {changes.length ? (
        <ul className="run-changes">
          {changes.map((item, at) => (
            <li key={at}>{`${item.kind_ko ?? ""} ${item.words_ko}`.trim()}</li>
          ))}
        </ul>
      ) : null}
      {words.korean && words.source ? (
        <details className="source-fold run-source" data-testid="run-source">
          <summary>{copy.source}</summary>
          <p lang="en" data-testid="run-source-text">{words.source}</p>
        </details>
      ) : null}
      {next && (next.korean || next.source) ? (
        // The explicit server label sits in the heading; legacy sentence prefixes stay out of the body.
        <p className="run-next" data-lang={next.korean ? "ko" : "source"} data-testid="run-next">
          <span className="run-next-label">{copy.nextLabel}{nextLabel ? ` · ${stepLabelWords(nextLabel)}` : ""}</span>
          {next.missing ? <span className="run-next-status">{` · ${translationNotice(nextTranslationStatus)}`}</span> : null}
          <span className="run-next-text" lang={next.korean ? undefined : "en"} data-testid="run-next-text">
            {next.korean ?? next.source}
          </span>
        </p>
      ) : null}
      {children}
    </section>
  );
}

function RunCard({ card }: { card: NonNullable<ReturnType<typeof useRun>["model"]["card"]> }) {
  if (!card.text && !card.values.length) return null;
  const head = { confirm: copy.card.confirm, clarify: copy.card.clarify, record: copy.card.record, answer: copy.card.answer }[card.kind];
  const listed = card.candidates.map((name, at) => `${at + 1}. ${name}`).join("\n");
  const text = card.candidates.length && card.text.endsWith(`\n${listed}`) ? card.text.slice(0, -listed.length - 1).trim() : card.text;
  return (
    <div className="run-card" data-kind={card.kind} data-testid="run-card">
      <p className="run-card-head">{head}</p>
      {text ? <p className="run-card-text" data-lang={card.source ? "source" : "ko"}>{text}</p> : null}
      {card.values.length ? (
        <ul className="run-card-values">
          {card.values.map((value) => (
            <li key={value}>{value}</li>
          ))}
        </ul>
      ) : null}
      {card.candidates.length ? (
        <div className="run-card-choices">
          {card.candidates.map((name) => (
            <button key={name} type="button" className="btn run-btn" onClick={() => chooseCandidate(card.turnId, name)}>
              {name}
            </button>
          ))}
        </div>
      ) : null}
      {card.kind === "confirm" ? <p className="muted">{copy.card.confirmSayHint}</p> : card.kind === "clarify" ? <p className="muted">{copy.card.sayHint}</p> : null}
    </div>
  );
}

function StopDialog({ disabled, testId = "run-stop" }: { disabled: boolean; testId?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Trigger asChild>
        <button type="button" className="btn run-btn run-stop" disabled={disabled} data-testid={testId}>
          <Square aria-hidden="true" />
          <span>{copy.stop}</span>
        </button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-backdrop run-dialog-backdrop" />
        <Dialog.Content className="dialog run-dialog" aria-describedby="run-stop-why">
          <Dialog.Title asChild>
            <h2>{copy.stopAsk}</h2>
          </Dialog.Title>
          <p id="run-stop-why">{copy.stopWhy}</p>
          <div className="dialog-actions">
            <Dialog.Close asChild>
              <button type="button" className="btn btn-quiet run-btn">
                {copy.stopCancel}
              </button>
            </Dialog.Close>
            <button
              type="button"
              className="btn run-btn run-stop"
              onClick={() => {
                setOpen(false);
                void stopRun();
              }}
              data-testid="run-stop-confirm"
            >
              {copy.stopConfirm}
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export function RunScreen({ session }: { session?: AuthSession } = {}) {
  const snapshot = useRun();
  const displayMode = useDisplayMode();
  const narrow = useNarrow();
  const now = useClock(500);
  useEffect(() => {
    // The scripts first, so a touch on this screen can resume the sound the browser holds back.
    void loadVoiceScripts().catch(() => undefined);
  }, []);
  useEffect(() => {
    document.body.dataset.run = snapshot.phase === "idle" && !snapshot.active ? "off" : "on";
    return () => {
      delete document.body.dataset.run;
    };
  }, [snapshot.phase, snapshot.active]);

  const { model } = snapshot;
  const protocol = model.protocol;
  const paused = isPaused(protocol);
  const word = listeningWord(snapshot.voice, paused, model.misheardAt, now);
  const timer = timerView(protocol, remainingOf);
  const figures = [...sourceFigures(protocol, model.screen), ...(model.figures.drawn ? [model.figures.drawn] : []), ...(model.figures.web ? [model.figures.web] : [])];
  const running = snapshot.active || snapshot.phase === "starting" || snapshot.voice === "RECONNECTING";

  if (snapshot.startRefusal && !running) {
    const step = REFUSAL_STEPS[snapshot.startRefusal.reason] ?? null;
    return (
      <div className="run-empty" data-testid="run-refused">
        <EmptyBox
          message={snapshot.startRefusal.words || copy.failed}
          action={
            <a className="btn btn-primary" href={routes.start(snapshot.startedProtocolId, { step })}>
              {copy.emptyAction}
            </a>
          }
        />
      </div>
    );
  }
  if (model.ended && !snapshot.active) return <EndScreen snapshot={snapshot} />;
  if (!running) {
    const failed = snapshot.voice === "ERROR";
    return (
      <div className="run-empty" data-testid="run-empty">
        {failed ? <Note>{snapshot.detail && !/websocket/i.test(snapshot.detail) ? snapshot.detail : copy.failed}</Note> : null}
        <EmptyBox message={copy.empty} action={<a className="btn btn-primary" href={routes.start()}>{copy.emptyAction}</a>} />
      </div>
    );
  }
  if (!protocol && snapshot.phase === "starting") {
    return (
      <div className="run" data-testid="run-screen" data-layout={narrow ? "narrow" : "wide"}>
        <div className="run-bar">
          <ListeningBadge word={word} misheard={model.misheard} />
        </div>
        <div className="run-main">
          <LoadingBox label={copy.connecting} />
        </div>
      </div>
    );
  }
  const misheardFresh = model.misheardAt !== null && now - model.misheardAt < MISHEARD_SHOWN_MS;
  if (displayMode === "far") {
    const words = protocol ? stepWords(protocol) : null;
    const farPrompt = paused
      ? copy.far.resume
      : model.card?.kind === "confirm"
        ? copy.far.confirm
        : model.card?.kind === "clarify"
          ? copy.far.clarify(model.card.candidates)
          : copy.far.continue;
    return (
      <div className="run run-far" data-testid="run-screen" data-display="far" data-paused={paused}>
        <p id="run-announcer" className="sr-only" aria-live="assertive" />
        <main className="run-far-main" id="main">
          {snapshot.micNotice && now - snapshot.micNotice.at < MIC_NOTICE_SHOWN_MS ? (
            <p className="run-far-mic-notice" role="status" data-testid="run-mic-notice">{snapshot.micNotice.text}</p>
          ) : null}
          <section className="run-far-step" aria-labelledby="run-far-step-label">
            <p className="run-far-label" id="run-far-step-label">{copy.far.step}{protocol?.current_step_label ? ` · ${stepLabelWords(protocol.current_step_label)}` : ""}</p>
            <p className="run-far-step-text" data-testid="run-far-step">
              {words?.korean ?? (model.screen?.translation_status === "rejected" ? copy.translationRejected : model.screen?.translation_status === "missing" ? copy.translationMissing : copy.translationPending)}
            </p>
          </section>
          {timer.kind !== "none" ? <div className="run-far-timer" data-testid="run-far-timer"><TimerChip timer={timer} /></div> : null}
          <section className="run-far-prompt" aria-labelledby="run-far-prompt-label">
            <p className="run-far-label" id="run-far-prompt-label">{copy.far.next}</p>
            <p data-testid="run-far-prompt">{farPrompt}</p>
            <p className="run-far-normal">{copy.far.normal}</p>
          </section>
        </main>
      </div>
    );
  }
  return (
    <div className="run" data-testid="run-screen" data-display="normal" data-layout={narrow ? "narrow" : "wide"} data-paused={paused} data-misheard={misheardFresh}>
      <p id="run-announcer" className="sr-only" aria-live="assertive" />
      <header className="run-bar">
        <p className="run-protocol">{protocol?.display_name ?? ""}</p>
        <ListeningBadge word={word} misheard={model.misheard} />
        <div className="run-controls">
          <span className="run-mic" data-on={snapshot.micOn}>
            {snapshot.micOn ? <Mic aria-hidden="true" /> : <MicOff aria-hidden="true" />}
          </span>
          <button type="button" className="btn run-btn" disabled={!snapshot.active} onClick={() => togglePause(paused)} data-testid="run-pause">
            {paused ? <Play aria-hidden="true" /> : <Pause aria-hidden="true" />}
            <span>{paused ? copy.resume : copy.pause}</span>
          </button>
          <StopDialog disabled={!snapshot.active} />
        </div>
      </header>
      <div className="run-alerts">
        {snapshot.audioHeld ? (
          <div className="run-alert" data-testid="run-audio-held">
            <p>{copy.audioHeld}</p>
            <button type="button" className="btn btn-primary run-btn" onClick={() => resumeSound()}>
              {copy.audioHeldButton}
            </button>
          </div>
        ) : null}
        {snapshot.micProblem ? (
          <div className="run-alert" data-testid="run-mic-problem">
            <p>{snapshot.micProblem}</p>
            <button type="button" className="btn btn-primary run-btn" onClick={() => reconnectMicrophone()}>
              {copy.micProblemButton}
            </button>
          </div>
        ) : null}
        {snapshot.micNotice && now - snapshot.micNotice.at < MIC_NOTICE_SHOWN_MS ? (
          <p className="run-alert run-mic-notice" role="status" data-testid="run-mic-notice">
            {snapshot.micNotice.text}
          </p>
        ) : null}
        {model.notice ? <Note testId="run-notice">{model.notice}</Note> : null}
        {model.filterNotice && now - model.filterNotice.at < FILTER_NOTICE_SHOWN_MS ? (
          <p className="run-alert run-filter-notice" role="status" data-testid="run-filter-notice">
            {model.filterNotice.text}
          </p>
        ) : null}
      </div>
      <main className="run-main" id="main">
        <StepPanel protocol={protocol} screen={model.screen}>
          {model.card && model.card.stepId === protocol?.current_step_id ? <RunCard card={model.card} /> : null}
          {model.timerNotice && (!model.timerNotice.stepId || model.timerNotice.stepId === protocol?.current_step_id) ? (
            <p className="run-timer-notice" data-testid="run-timer-notice">
              <Timer aria-hidden="true" />
              <span>{model.timerNotice.text}</span>
            </p>
          ) : null}
        </StepPanel>
        {narrow ? <TimerZone timers={[timer]} narrow /> : null}
        {narrow ? <FigurePanel figures={figures} /> : null}
        <p className="run-voice-hint">{copy.voiceHint}</p>
        <RunTools session={session ?? { mode: "accounts", signed_in: true, account: null }} />
      </main>
      {narrow ? null : (
        <div className="run-side" data-testid="run-side">
          <TimerZone timers={[timer]} narrow={false} />
          <FigurePanel figures={figures} />
        </div>
      )}
      {/* The records fold under the step on every screen: their head and [메모] / [사진] stay in sight. */}
      <RecordsPanel experimentId={snapshot.experimentId} refreshKey={recordsKey(model)} narrow />
      <CaptionBar caption={model.caption} log={model.log} />
      {paused ? (
        // Person's decision 1 (2026-10-26): while paused, [다시 시작] and [종료] both work; a refusal the server sends is
        // said here, where the person is looking, not under the band.
        <div className="run-paused" role="alert" data-testid="run-paused">
          <p>{copy.pausedBand}</p>
          {model.notice ? (
            <p className="run-paused-note" role="status" data-testid="run-paused-note">
              {model.notice}
            </p>
          ) : null}
          <div className="run-paused-actions">
            <button type="button" className="btn run-btn run-paused-resume" onClick={() => togglePause(true)} data-testid="run-resume">
              <Play aria-hidden="true" />
              <span>{copy.resume}</span>
            </button>
            <StopDialog disabled={!snapshot.active} testId="run-paused-stop" />
          </div>
          <p className="run-paused-hint">{copy.pausedStopHint}</p>
        </div>
      ) : null}
    </div>
  );
}

/** The timeline is read again when a turn recorded something (its card) and when the step moves. */
function recordsKey(model: ReturnType<typeof useRun>["model"]): string {
  const card = model.card;
  return `${model.protocol?.current_step_id ?? ""}:${card && card.kind === "record" ? card.turnId : ""}`;
}
