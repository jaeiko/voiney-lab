import { useQuery } from "@tanstack/react-query";
import { Angry, Check, CircleCheckBig, Frown, Laugh, Meh, NotebookPen, Smile, type LucideIcon } from "lucide-react";
import { useState } from "react";

import { ApiError, postJson } from "../../api/client";
import { runApi } from "../../api/runApi";
import { Note } from "../../components/Pieces";
import { run as copy } from "../../copy/ko";
import { routes } from "../../lib/navigation";
import type { ProtocolState } from "../../voice/runModel";
import type { RunSnapshot } from "../../voice/runStore";
import { recordLines } from "./RecordsPanel";

// The end of an experiment (lane RX4, person's decision 3 of 2026-10-26, after the
// person's mock-up "실험 끝 · 피드백"): what was done at the top with the notebook
// a press away, three numbers from the run itself, then one question -- five
// large tiles for how the guidance felt, chips for what it was like, a line of
// words -- and [보내기] or [건너뛰기]. The answer goes where lane LOG's did
// (POST /api/pilot/feedback, kept with the experiment record); the chips are
// its `tags`. An experiment answered or skipped is not asked about again on
// this device (the same key the old screen keeps).

const ANSWERED_KEY = "voineyFeedbackAnswered";
export const TAG_CODES = ["heard_well", "missed_often", "translation_awkward", "screen_easy", "timer_easy", "figure_helped"] as const;
export type TagCode = (typeof TAG_CODES)[number];
const FACES: LucideIcon[] = [Angry, Frown, Meh, Smile, Laugh];

function answeredIds(): string[] {
  try {
    const ids = JSON.parse(globalThis.localStorage?.getItem(ANSWERED_KEY) || "[]");
    return Array.isArray(ids) ? ids.filter((id): id is string => typeof id === "string") : [];
  } catch {
    return [];
  }
}

function rememberAnswered(id: string): void {
  try {
    const ids = answeredIds().filter((item) => item !== id);
    ids.push(id);
    globalThis.localStorage?.setItem(ANSWERED_KEY, JSON.stringify(ids.slice(-50)));
  } catch {
    /* a browser that keeps nothing may ask once more; the server keeps the last answer */
  }
}

/** How long the run took, in words: from the server's experiment clock (its start, and its end when it has one), else from the start to now. */
export function tookWords(protocol: ProtocolState | null, endedAtMs: number): string | null {
  const clock = protocol?.timers?.experiment as { started_at?: unknown; ended_at?: unknown } | null | undefined;
  const started = Date.parse(typeof clock?.started_at === "string" ? clock.started_at : "");
  if (!Number.isFinite(started)) return null;
  const ended = Date.parse(typeof clock?.ended_at === "string" ? clock.ended_at : "");
  const seconds = Math.max(0, Math.round(((Number.isFinite(ended) ? ended : endedAtMs) - started) / 1000));
  if (seconds < 60) return copy.end.took(copy.end.seconds(seconds));
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return copy.end.took(copy.end.minutes(minutes));
  return copy.end.took(copy.end.hours(Math.floor(minutes / 60), minutes % 60));
}

/** The step line: all of them when the run completed, else up to the step it ended at. */
export function stepsWords(status: string | null, protocol: ProtocolState | null): string | null {
  if (!protocol || !Number.isInteger(protocol.total_steps) || protocol.total_steps <= 0) return null;
  if (status === "completed") return copy.end.stepsAll(protocol.total_steps);
  const label = protocol.current_step_label?.trim();
  return label ? copy.end.stepsDone(label, protocol.total_steps) : null;
}

export interface FeedbackBody {
  report_id: string;
  rating: number | null;
  skipped: boolean;
  note: string | null;
  tags: TagCode[];
}

export function EndScreen({ snapshot, now = () => Date.now() }: { snapshot: RunSnapshot; now?: () => number }) {
  const { model } = snapshot;
  const ended = model.ended;
  const protocol = model.protocol;
  // When this screen first showed: the end of a run the server gave no end time for (a session.stop).
  const [endedAt] = useState(() => now());
  const experimentId = snapshot.experimentId;
  const timeline = useQuery({
    queryKey: ["workspace", "experiments", experimentId ?? "", "timeline", "end"],
    queryFn: () => runApi.timeline(experimentId ?? ""),
    enabled: Boolean(experimentId),
    retry: false,
  });
  const voiceCount = timeline.data ? recordLines(timeline.data.timeline).filter((line) => line.kind === "voice").length : null;
  const changesCount = Array.isArray(protocol?.run_changes) ? protocol!.run_changes!.length : 0;
  const helpCount = model.helpTurns.length;
  const status = ended?.status ?? null;
  const sub = [protocol?.display_name ?? null, stepsWords(status, protocol), tookWords(protocol, endedAt)].filter((part): part is string => Boolean(part));
  const notebookHref = experimentId ? routes.record(experimentId) : routes.records;

  const reportId = model.reportId;
  const [asked] = useState(() => Boolean(reportId) && !answeredIds().includes(reportId as string));
  const [rating, setRating] = useState<number | null>(null);
  const [tags, setTags] = useState<TagCode[]>([]);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<"sent" | "skipped" | null>(null);
  const [failed, setFailed] = useState(false);

  async function post(body: FeedbackBody, outcome: "sent" | "skipped") {
    if (busy) return;
    setBusy(true);
    setFailed(false);
    try {
      await postJson<unknown>("/api/pilot/feedback", body);
      rememberAnswered(body.report_id);
      setDone(outcome);
    } catch (error) {
      // A record the server no longer rates (another lab's, gone) is not asked about again either.
      if (error instanceof ApiError && error.status === 404) {
        rememberAnswered(body.report_id);
        setDone("skipped");
      } else setFailed(true);
    } finally {
      setBusy(false);
    }
  }

  const counts: Array<{ key: string; label: string; value: number | null }> = [
    { key: "voice", label: copy.end.counts.voice, value: voiceCount },
    { key: "changes", label: copy.end.counts.changes, value: changesCount },
    { key: "help", label: copy.end.counts.help, value: helpCount },
  ];

  return (
    <section className="end" aria-labelledby="end-title" data-testid="run-ended" data-status={status ?? undefined}>
      <header className="end-head">
        <div>
          <h1 className="end-title" id="end-title">
            <CircleCheckBig aria-hidden="true" />
            <span>{status === "completed" ? copy.end.completed : copy.end.stopped}</span>
          </h1>
          {sub.length ? <p className="end-sub" data-testid="end-sub">{sub.join(" · ")}</p> : null}
          {ended?.report === "saved" || ended?.report === "failed" ? <p className="end-sub">{ended.report === "saved" ? copy.end.saved : copy.end.failed}</p> : null}
        </div>
        <a className="btn" href={notebookHref} data-testid="end-notebook">
          <NotebookPen aria-hidden="true" />
          <span>{copy.end.notebook}</span>
        </a>
      </header>
      <ul className="end-counts" data-testid="end-counts">
        {counts.map((item) => (
          <li key={item.key} className="end-count" data-count={item.key}>
            <span className="end-count-number">{item.value === null ? copy.end.countUnknown : item.value}</span>
            <span className="end-count-label">{item.label}</span>
          </li>
        ))}
      </ul>
      {asked && reportId && done === null ? (
        <div className="end-question" data-testid="end-question">
          <h2 id="end-question-title">{copy.end.question}</h2>
          <p className="muted">{copy.end.questionLead}</p>
          <div className="end-ratings" role="radiogroup" aria-labelledby="end-question-title" data-testid="end-ratings">
            {copy.end.ratings.map((word, at) => {
              const Face = FACES[at];
              const value = at + 1;
              return (
                <button key={word} type="button" role="radio" aria-checked={rating === value} className="end-rating" data-rating={value} onClick={() => setRating(value)} disabled={busy}>
                  <Face aria-hidden="true" />
                  <span>{word}</span>
                </button>
              );
            })}
          </div>
          <p className="end-chips-lead" id="end-chips-lead">{copy.end.chipsLead}</p>
          <div className="end-chips" role="group" aria-labelledby="end-chips-lead" data-testid="end-chips">
            {TAG_CODES.map((code) => {
              const on = tags.includes(code);
              return (
                <button key={code} type="button" className="end-chip" aria-pressed={on} data-tag={code} disabled={busy} onClick={() => setTags(on ? tags.filter((item) => item !== code) : [...tags, code])}>
                  {on ? <Check aria-hidden="true" /> : null}
                  <span>{copy.end.chips[code]}</span>
                </button>
              );
            })}
          </div>
          <label className="field end-note">
            <span>{copy.end.noteLabel}</span>
            <input type="text" maxLength={200} value={note} disabled={busy} onChange={(event) => setNote(event.target.value)} data-testid="end-note" />
          </label>
          {failed ? <Note testId="end-failed">{copy.end.sendFailed}</Note> : null}
          <div className="end-actions">
            <button type="button" className="btn btn-text" disabled={busy} onClick={() => void post({ report_id: reportId, rating: null, skipped: true, note: null, tags: [] }, "skipped")} data-testid="end-skip">
              {copy.end.skip}
            </button>
            <button
              type="button"
              className="btn btn-primary end-send"
              disabled={busy || rating === null}
              onClick={() => void post({ report_id: reportId, rating, skipped: false, note: note.trim() || null, tags: TAG_CODES.filter((code) => tags.includes(code)) }, "sent")}
              data-testid="end-send"
            >
              {busy ? copy.end.sending : copy.end.send}
            </button>
          </div>
        </div>
      ) : (
        <div className="end-after">
          {done === "sent" ? (
            <p className="saved" role="status" data-testid="end-thanks">
              <Check aria-hidden="true" />
              <span>{copy.end.thanks}</span>
            </p>
          ) : null}
          <a className="btn btn-primary btn-big" href={routes.home} data-testid="end-home">
            {copy.end.home}
          </a>
        </div>
      )}
    </section>
  );
}
