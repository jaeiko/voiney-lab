// What the analysis screen says while a run goes (lane RX1, decision 1):
// the elapsed clock, the pieces with their pages, and -- only from measured
// progress -- how much longer the pieces left would take at the pace so
// far. Nothing here states a number the server did not give or the clock
// did not measure: with no piece done yet there is no estimate.
import type { AnalysisChunk, AnalysisRun, ProtocolDetail } from "../api/types";
import { analysis as words } from "../copy/ko";
import { FAILED_STATES, RUNNING_STATES } from "./protocolStatus";

export type RunPhase = "ocr" | "running" | "ready" | "review" | "failed" | "blocked" | "idle";

export interface PieceLine {
  text: string;
  status: "done" | "running" | "waiting" | "failed";
}

export function elapsedClock(since: string | null | undefined, now: number = Date.now()): string | null {
  if (!since) return null;
  const started = Date.parse(since);
  if (!Number.isFinite(started) || started > now) return null;
  const total = Math.floor((now - started) / 1000);
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

export function pieceLines(chunks: AnalysisChunk[] | undefined): PieceLine[] {
  if (!Array.isArray(chunks)) return [];
  return chunks
    .filter((chunk) => chunk && typeof chunk === "object")
    .map((chunk) => {
      const first = chunk.source_page_start;
      const last = chunk.source_page_end;
      const pages = Number.isInteger(first) && Number.isInteger(last) ? words.piecePages(first as number, last as number) : null;
      const raw = String(chunk.status ?? "");
      const status: PieceLine["status"] =
        raw === "completed" || raw === "complete" || raw === "done" || raw === "passed"
          ? "done"
          : raw === "failed"
            ? "failed"
            : raw === "in_progress" || raw === "started" || raw === "running" || raw === "retrying"
              ? "running"
              : "waiting";
      const state = { done: words.pieceDone, running: words.pieceRunning, waiting: words.pieceWaiting, failed: words.pieceFailed }[status];
      return { text: pages ? `${pages} · ${state}` : state, status };
    });
}

/**
 * Minutes the pieces left would take at the pace so far, or null without a
 * measured pace (no piece done, a single piece, no request time).
 */
export function estimatedMinutesLeft(run: AnalysisRun | null | undefined, now: number = Date.now()): number | null {
  if (!run) return null;
  const total = run.total_chunks ?? 0;
  const done = run.completed_chunks ?? 0;
  if (!Number.isInteger(total) || !Number.isInteger(done) || total < 2 || done < 1 || done >= total) return null;
  const started = Date.parse(run.requested_at ?? "");
  if (!Number.isFinite(started) || started > now) return null;
  const perPiece = (now - started) / done;
  return Math.max(0, Math.ceil((perPiece * (total - done)) / 60000));
}

export function estimateWords(minutes: number | null): string | null {
  if (minutes === null) return null;
  return minutes < 1 ? words.estimateSoon : words.estimate(minutes);
}

export function runPhase(detail: ProtocolDetail | null | undefined): RunPhase {
  if (!detail) return "idle";
  const status = detail.analysis_status ?? "";
  const run = detail.analysis_run ?? null;
  const runState = run?.state ?? "";
  if (detail.available_for_execution === true && !(run?.reanalysis === true && RUNNING_STATES.has(runState))) return "ready";
  if (status === "ocr_in_progress" || status === "ocr_required" || runState === "ocr_in_progress") return "ocr";
  if (detail.review_pending && Number.isInteger(detail.review_pending.needs_review_count)) return "review";
  if (RUNNING_STATES.has(runState) || RUNNING_STATES.has(status) || RUNNING_STATES.has(detail.lifecycle_state ?? "")) return "running";
  if (FAILED_STATES.has(runState) || FAILED_STATES.has(status) || (typeof run?.failure_code === "string" && run.failure_code)) return "failed";
  if (status === "structured_analysis_ready" || status === "chunked_analysis_required" || status === "uploaded") return "idle";
  return "blocked";
}

export function failureWords(code: string | null | undefined): string {
  const table = words.failures as Record<string, string>;
  if (code && table[code]) return table[code];
  // The server's failure codes are many; a family is read from the word in them.
  if (code?.includes("timeout")) return words.failures.analysis_timeout;
  if (code?.includes("provider")) return words.failures.provider_error;
  if (code?.includes("evidence") || code?.includes("source")) return words.failures.evidence_mismatch;
  if (code?.includes("response") || code?.includes("schema") || code?.includes("json")) return words.failures.invalid_response;
  if (code?.includes("merge")) return words.failures.merge_conflict;
  if (code?.includes("cancel")) return words.failures.analysis_cancelled;
  return words.failures.unknown;
}
