// What a protocol's row says and offers, from the server's fields alone
// (lane RX0). The states are the old screen's (static/index.html
// protocolListBadges / catalogStatusWord): the words are new, the facts the
// same. Nothing here guesses from prose, and no expected time is shown --
// the server gives none.
import type { AnalysisRun, CatalogEntry } from "../api/types";
import { protocols as words, time as timeWords } from "../copy/ko";

export const RUNNING_STATES = new Set([
  "analysis_pending",
  "analyzing",
  "chunk_planned",
  "chunk_analysis_in_progress",
  "merge_in_progress",
]);
export const FAILED_STATES = new Set([
  "analysis_failed",
  "chunk_analysis_failed",
  "merge_conflict",
  "chunk_analysis_cancelled",
]);
const OCR_RUNNING = new Set(["ocr_in_progress"]);
const OCR_CHECK = new Set(["ocr_required", "ocr_review_required"]);
const OCR_FAILED = new Set(["ocr_rejected", "ocr_failed"]);

export type StatusKind = "ready" | "review" | "analysing" | "failed" | "ocr" | "blocked";
export type ActionKind = "start" | "review" | "retry";

export interface Chip {
  text: string;
  kind: "ready" | "review" | "analysing" | "failed" | "quiet";
}

export interface ProtocolStatus {
  kind: StatusKind;
  chips: Chip[];
  action: ActionKind | null;
  reviewCount: number | null;
}

export function reviewCount(entry: CatalogEntry): number | null {
  const count = entry.review_pending?.needs_review_count;
  return Number.isInteger(count) ? (count as number) : null;
}

export function runRunning(run: AnalysisRun | null | undefined): boolean {
  return Boolean(run && typeof run.state === "string" && RUNNING_STATES.has(run.state));
}

export function entryAnalysing(entry: CatalogEntry, run?: AnalysisRun | null): boolean {
  return (
    RUNNING_STATES.has(entry.lifecycle_state ?? "") ||
    RUNNING_STATES.has(entry.analysis_status ?? "") ||
    OCR_RUNNING.has(entry.analysis_status ?? "") ||
    runRunning(run)
  );
}

/** "3분 지남" from an ISO time, or null when the time is unknown or in the future. */
export function elapsedWords(since: string | null | undefined, now: number = Date.now()): string | null {
  if (!since) return null;
  const started = Date.parse(since);
  if (!Number.isFinite(started) || started > now) return null;
  const minutes = Math.floor((now - started) / 60000);
  if (minutes < 1) return timeWords.justNow;
  if (minutes < 120) return timeWords.minutesAgo(minutes);
  return timeWords.hoursAgo(Math.floor(minutes / 60));
}

function progressWords(run: AnalysisRun | null | undefined, now: number): string[] {
  const parts: string[] = [];
  const elapsed = elapsedWords(run?.requested_at, now);
  if (elapsed) parts.push(elapsed);
  const total = run?.total_chunks ?? 0;
  const done = run?.completed_chunks ?? 0;
  if (Number.isInteger(total) && total > 1) parts.push(words.chips.chunks(Math.max(0, done), total));
  return parts;
}

export function protocolStatus(
  entry: CatalogEntry,
  run: AnalysisRun | null | undefined = null,
  now: number = Date.now(),
): ProtocolStatus {
  const chips: Chip[] = [];
  const pending = reviewCount(entry);
  let kind: StatusKind;
  let action: ActionKind | null;
  const running = runRunning(run);
  const status = entry.analysis_status ?? "";

  if (entry.available_for_execution === true) {
    kind = "ready";
    action = "start";
    chips.push({ text: words.chips.ready, kind: "ready" });
    if (run?.reanalysis === true && running) {
      chips.push({ text: [words.chips.reanalysing, ...progressWords(run, now)].join(" · "), kind: "analysing" });
    } else if (run?.reanalysis === true && typeof run.failure_code === "string" && run.failure_code) {
      chips.push({ text: words.chips.failed, kind: "failed" });
    }
  } else if (OCR_RUNNING.has(status)) {
    kind = "analysing";
    action = null;
    chips.push({ text: words.chips.ocrRunning, kind: "analysing" });
  } else if (entryAnalysing(entry, run)) {
    kind = "analysing";
    action = null;
    chips.push({ text: [words.chips.analysing, ...progressWords(run, now)].join(" · "), kind: "analysing" });
  } else if (FAILED_STATES.has(status)) {
    kind = "failed";
    action = "retry";
    chips.push({ text: words.chips.failed, kind: "failed" });
  } else if (pending !== null) {
    kind = "review";
    action = "review";
  } else if (OCR_CHECK.has(status)) {
    kind = "ocr";
    action = "review";
    chips.push({ text: words.chips.ocrCheck, kind: "review" });
  } else if (OCR_FAILED.has(status)) {
    kind = "ocr";
    action = "retry";
    chips.push({ text: words.chips.ocrFailed, kind: "failed" });
  } else if (entry.execution_blocked_reason === "usage_scope_not_development") {
    kind = "blocked";
    action = null;
    chips.push({ text: words.chips.exampleOnly, kind: "quiet" });
  } else {
    kind = "blocked";
    action = null;
    chips.push({ text: words.chips.blocked, kind: "quiet" });
  }

  if (pending !== null) chips.push({ text: words.chips.reviewLeft(pending), kind: "review" });
  if (entry.development_only === true) chips.push({ text: words.chips.development, kind: "quiet" });
  // Server sentences shown as they are: the "analyse again" advice and the review version that runs.
  if (typeof entry.analysis_outdated_ko === "string" && entry.analysis_outdated_ko.trim()) {
    chips.push({ text: entry.analysis_outdated_ko.trim(), kind: "quiet" });
  }
  const version = entry.current_review_version?.version_ko;
  if (typeof version === "string" && version.trim()) chips.push({ text: version.trim(), kind: "quiet" });

  return { kind, chips, action, reviewCount: pending };
}

/** Runnable first (lane PRE's rule), then the newest upload first. */
export function sortEntries(entries: CatalogEntry[]): CatalogEntry[] {
  const when = (entry: CatalogEntry) => {
    const at = Date.parse(entry.created_at ?? "");
    return Number.isFinite(at) ? at : -Infinity;
  };
  return [...entries].sort((a, b) => {
    const ra = a.available_for_execution === true ? 0 : 1;
    const rb = b.available_for_execution === true ? 0 : 1;
    if (ra !== rb) return ra - rb;
    return when(b) - when(a);
  });
}

/** "10월 19일" from an ISO time in the researcher's own time zone, or null. */
export function uploadedDay(createdAt: string | null | undefined): string | null {
  if (!createdAt) return null;
  const at = Date.parse(createdAt);
  if (!Number.isFinite(at)) return null;
  const date = new Date(at);
  return `${date.getMonth() + 1}월 ${date.getDate()}일`;
}
