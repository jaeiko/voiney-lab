import * as Dialog from "@radix-ui/react-dialog";
import { AudioLines, CircleAlert, Ear, Hourglass, Loader, Mic, MicOff, Pause, Play, Square, Timer, Volume2 } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";

import { EmptyBox, LoadingBox, Note } from "../components/Pieces";
import { run as copy } from "../copy/ko";
import { oldRoutes, routes } from "../lib/navigation";
import { chooseCandidate, loadVoiceScripts, reconnectMicrophone, resumeSound, serverNow, stopRun, togglePause } from "../voice/engine";
import { FILTER_NOTICE_SHOWN_MS, isPaused, listeningWord, MISHEARD_SHOWN_MS, plain, sourceFigures, timerView, type ListeningWord, type ProtocolState, type TimerView } from "../voice/runModel";
import { useRun } from "../voice/runStore";
import { CaptionBar } from "./run/CaptionBar";
import { FigurePanel } from "./run/FigurePanel";
import { RecordsPanel } from "./run/RecordsPanel";

// The experiment screen (lane RX2, person's decision 2 of 2026-10-25): the
// step large in the middle (Korean first, the source folded), the pictures
// large, the timer only when there is one (a small chip, large from its last
// minute), the caption in two whole lines that opens the conversation, the
// records beside it with [메모] and [사진] always on the screen, the one
// listening word large, and [일시정지] / [종료]. While paused a band across
// the whole screen says how to go on. With no experiment there is no run bar.
// Everything drawn is the server's last state (runModel.ts).

const NARROW = "(max-width: 899px)";
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

function ListeningBadge({ word }: { word: ListeningWord }) {
  const Icon = LISTENING_ICONS[word];
  return (
    <div className="run-listening" data-word={word} role="status" aria-live="polite" data-testid="run-listening">
      <Icon aria-hidden="true" />
      <span>{copy.listening[word]}</span>
      {word === "misheard" ? <span className="run-listening-hint">{copy.misheardHint}</span> : null}
    </div>
  );
}

function TimerChip({ timer }: { timer: TimerView }) {
  if (timer.kind === "none") return null;
  const big = timer.kind === "soon" || timer.kind === "done";
  const words = timer.kind === "done" ? copy.timer.over(clock(timer.seconds)) : timer.kind === "idle" ? copy.timer.idle(clock(timer.seconds)) : copy.timer.left(clock(timer.seconds));
  const fill = timer.kind === "running" || timer.kind === "soon" ? (timer.duration > 0 ? Math.min(100, (timer.seconds / timer.duration) * 100) : 0) : timer.kind === "idle" ? 100 : 0;
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

function StepPanel({ protocol, nextSentence, children }: { protocol: ProtocolState | null; nextSentence: string | null; children?: ReactNode }) {
  if (!protocol || !protocol.current_step_label) {
    return (
      <section className="run-step" data-testid="run-step">
        <p className="run-step-text run-step-waiting">{copy.waiting}</p>
      </section>
    );
  }
  const korean = protocol.primary_summary?.trim() ? plain(protocol.primary_summary) : null;
  const source = protocol.display_summary?.trim() ? plain(protocol.display_summary) : null;
  const body = korean ?? source ?? "";
  const length = body.length > 120 ? "long" : body.length > 60 ? "medium" : "short";
  const changes = (protocol.current_step_run_changes ?? []).filter((item) => item?.words_ko);
  return (
    <section className="run-step" aria-labelledby="run-step-label" data-testid="run-step">
      <p className="run-step-label" id="run-step-label">{copy.step(protocol.current_step_label, protocol.total_steps)}</p>
      <p className="run-step-text" data-length={length} data-testid="run-step-text">{body}</p>
      {changes.length ? (
        <ul className="run-changes">
          {changes.map((item, at) => (
            <li key={at}>{`${item.kind_ko ?? ""} ${item.words_ko}`.trim()}</li>
          ))}
        </ul>
      ) : null}
      {korean && source ? (
        <details className="source-fold run-source">
          <summary>{copy.source}</summary>
          <p lang="en">{source}</p>
        </details>
      ) : null}
      {nextSentence ? <p className="run-next">{copy.next(plain(nextSentence).replace(/\s+/g, " ").trim())}</p> : null}
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
      {text ? <p className="run-card-text">{text}</p> : null}
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
      {card.kind === "confirm" || card.kind === "clarify" ? <p className="muted">{copy.card.sayHint}</p> : null}
    </div>
  );
}

function StopDialog({ disabled }: { disabled: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Trigger asChild>
        <button type="button" className="btn run-btn run-stop" disabled={disabled} data-testid="run-stop">
          <Square aria-hidden="true" />
          <span>{copy.stop}</span>
        </button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-backdrop" />
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

export function RunScreen() {
  const snapshot = useRun();
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
  const figures = [...sourceFigures(protocol, model.screen), ...(model.figures.drawn ? [model.figures.drawn] : [])];
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
  if (model.ended && !snapshot.active) {
    return (
      <div className="run-empty" data-testid="run-ended">
        <div className="state-box" data-state="empty">
          <p>{model.ended.status === "completed" ? copy.ended.completed : copy.ended.stopped}</p>
          {model.ended.report ? <p className="muted">{model.ended.report === "saved" ? copy.ended.saved : copy.ended.failed}</p> : null}
          <div className="secondary-actions">
            <a className="btn btn-primary" href={snapshot.experimentId ? oldRoutes.record(snapshot.experimentId) : oldRoutes.records}>
              {copy.ended.records}
            </a>
            <a className="btn" href={routes.home}>
              {copy.ended.home}
            </a>
          </div>
        </div>
      </div>
    );
  }
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
          <ListeningBadge word={word} />
        </div>
        <div className="run-main">
          <LoadingBox label={copy.connecting} />
        </div>
      </div>
    );
  }
  const misheardFresh = model.misheardAt !== null && now - model.misheardAt < MISHEARD_SHOWN_MS;
  return (
    <div className="run" data-testid="run-screen" data-layout={narrow ? "narrow" : "wide"} data-paused={paused} data-misheard={misheardFresh}>
      <p id="run-announcer" className="sr-only" aria-live="assertive" />
      <header className="run-bar">
        <p className="run-protocol">{protocol?.display_name ?? ""}</p>
        <ListeningBadge word={word} />
        {timer.kind !== "none" && timer.kind !== "soon" && timer.kind !== "done" ? <TimerChip timer={timer} /> : null}
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
        {model.notice ? <Note testId="run-notice">{model.notice}</Note> : null}
        {model.filterNotice && now - model.filterNotice.at < FILTER_NOTICE_SHOWN_MS ? (
          <p className="run-alert run-filter-notice" role="status" data-testid="run-filter-notice">
            {model.filterNotice.text}
          </p>
        ) : null}
      </div>
      <main className="run-main" id="main">
        {timer.kind === "soon" || timer.kind === "done" ? <TimerChip timer={timer} /> : null}
        <StepPanel protocol={protocol} nextSentence={model.screen?.next_step_sentence ?? null}>
          {model.card && model.card.stepId === protocol?.current_step_id ? <RunCard card={model.card} /> : null}
          {model.timerNotice && (!model.timerNotice.stepId || model.timerNotice.stepId === protocol?.current_step_id) ? (
            <p className="run-timer-notice" data-testid="run-timer-notice">
              <Timer aria-hidden="true" />
              <span>{model.timerNotice.text}</span>
            </p>
          ) : null}
        </StepPanel>
        <FigurePanel figures={figures} />
      </main>
      <RecordsPanel experimentId={snapshot.experimentId} refreshKey={recordsKey(model)} narrow={narrow} />
      <CaptionBar caption={model.caption} log={model.log} />
      {paused ? (
        <div className="run-paused" role="alert" data-testid="run-paused">
          <p>{copy.pausedBand}</p>
          <button type="button" className="btn run-btn run-paused-resume" onClick={() => togglePause(true)} data-testid="run-resume">
            <Play aria-hidden="true" />
            <span>{copy.resume}</span>
          </button>
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
